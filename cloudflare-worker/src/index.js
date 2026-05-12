const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

const CONFIG = {
  EMAIL_DOMAIN: "boomstyleai.com",
  JWT_SECRET: "-BlpBvb3zVDTD_Lp1qKZQkdZZhHg7OpMZEoymq62M2uK-qzU_J5mXbyTzz4SidFq",
  JWT_TTL_SECONDS: 24 * 60 * 60,
  MAILBOX_TTL_SECONDS: 24 * 60 * 60,
  MAILBOX_INDEX_MAX: 2000,
  MAX_MESSAGES: 20,
  MAX_RAW_BYTES: 64 * 1024,
};

const MAILBOX_PREFIX = "mailbox:";
const MAILBOX_BINDING = "mailboxes";
const MAILBOX_INDEX_KEY = "mailbox_index_recent.json";

export default {
  async fetch(request, env) {
    return handleFetch(request, env);
  },

  async email(message, env) {
    await handleEmail(message, env);
  },
};

async function handleFetch(request, env) {
  try {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(request),
      });
    }

    if (request.method === "GET" && url.pathname === "/healthz") {
      return jsonResponse(
        {
          ok: true,
          emailDomain: getConfig(env, "EMAIL_DOMAIN") || null,
        },
        200,
        request,
      );
    }

    if (request.method === "POST" && url.pathname === "/admin/new_address") {
      return handleCreateAddress(request, env);
    }

    if (request.method === "GET" && url.pathname === "/admin/mailboxes") {
      return handleAdminListMailboxes(request, env);
    }

    if (request.method === "GET" && url.pathname === "/api/mails") {
      return handleListMails(request, env);
    }

    return jsonResponse({ error: "Not Found" }, 404, request);
  } catch (error) {
    return jsonResponse(
      {
        error: "Internal Error",
        detail: error?.message || String(error),
      },
      500,
      request,
    );
  }
}

async function handleCreateAddress(request, env) {
  if (!isValidAdminRequest(request, env)) {
    return jsonResponse({ error: "Unauthorized" }, 401, request);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ error: "Invalid JSON body" }, 400, request);
  }

  const configuredDomain = normalizeDomain(getConfig(env, "EMAIL_DOMAIN"));
  const requestedDomain = normalizeDomain(body?.domain);

  if (!configuredDomain) {
    return jsonResponse({ error: "EMAIL_DOMAIN is not configured" }, 500, request);
  }
  if (requestedDomain !== configuredDomain) {
    return jsonResponse(
      {
        error: "Domain mismatch",
        expected: configuredDomain,
      },
      400,
      request,
    );
  }

  const localPart = resolveLocalPart(body);
  if (!isSafeLocalPart(localPart)) {
    return jsonResponse({ error: "Invalid mailbox name" }, 400, request);
  }

  const address = `${localPart}@${configuredDomain}`;
  const now = Math.floor(Date.now() / 1000);
  const mailboxTtl = parsePositiveInt(
    getConfig(env, "MAILBOX_TTL_SECONDS"),
    CONFIG.MAILBOX_TTL_SECONDS,
  );
  const expiresAt = now + mailboxTtl;

  const mailbox = {
    address,
    createdAt: new Date(now * 1000).toISOString(),
    expiresAt: new Date(expiresAt * 1000).toISOString(),
    expiresAtUnix: expiresAt,
    messages: [],
  };

  const jwt = await signJwt(
    {
      iss: "cf-mail-worker",
      sub: address,
      address,
      iat: now,
      exp: now + parsePositiveInt(getConfig(env, "JWT_TTL_SECONDS"), CONFIG.JWT_TTL_SECONDS),
    },
    getConfig(env, "JWT_SECRET"),
  );

  await saveMailbox(env, mailbox);
  await updateMailboxIndex(env, mailbox);

  return jsonResponse(
    {
      address,
      jwt,
      created_at: mailbox.createdAt,
      expires_at: mailbox.expiresAt,
    },
    200,
    request,
  );
}

async function handleAdminListMailboxes(request, env) {
  if (!isValidAdminRequest(request, env)) {
    return jsonResponse({ error: "Unauthorized" }, 401, request);
  }

  const url = new URL(request.url);
  const limit = clamp(parsePositiveInt(url.searchParams.get("limit"), 100), 1, 500);
  const sinceValue = String(url.searchParams.get("since") || "").trim();
  const sinceTs = sinceValue ? Date.parse(sinceValue) : NaN;
  const query = normalizeAddress(url.searchParams.get("q") || "");
  const searchAll = query && String(url.searchParams.get("search_all") || "") === "1";

  let recentEntries = await loadMailboxIndex(env);
  let selectedEntries = [];

  if (query) {
    const recentMatches = recentEntries.filter((entry) => matchesMailboxQuery(entry, query));
    selectedEntries = await resolveMailboxEntries(env, recentMatches, limit);
    if (searchAll && selectedEntries.length < limit) {
      const seen = new Set(selectedEntries.map((entry) => entry.address));
      const scannedEntries = await searchMailboxEntriesByQuery(
        env,
        query,
        limit - selectedEntries.length,
        seen,
      );
      selectedEntries = [...selectedEntries, ...scannedEntries];
    }
  } else {
    selectedEntries = recentEntries;
  }

  selectedEntries = selectedEntries.filter((entry) => {
    const createdAtTs = Date.parse(String(entry.createdAt || ""));
    if (Number.isFinite(sinceTs) && (!Number.isFinite(createdAtTs) || createdAtTs <= sinceTs)) {
      return false;
    }
    return true;
  }).slice(0, limit);

  const now = Math.floor(Date.now() / 1000);
  const items = await Promise.all(
    selectedEntries.map(async (entry) => ({
      address: entry.address,
      created_at: entry.createdAt || "",
      expires_at: entry.expiresAt || "",
      last_message_at: entry.lastMessageAt || "",
      message_count: Number(entry.messageCount || 0),
      jwt: await issueMailboxJwt(env, entry.address, now),
    })),
  );

  return jsonResponse(
    {
      items,
      total: items.length,
      source_total: recentEntries.length,
      since: sinceValue || null,
      query: query || null,
      search_all: Boolean(searchAll),
    },
    200,
    request,
  );
}

async function handleListMails(request, env) {
  const token = extractBearerToken(request);
  if (!token) {
    return jsonResponse({ error: "Missing bearer token" }, 401, request);
  }

  let payload;
  try {
    payload = await verifyJwt(token, getConfig(env, "JWT_SECRET"));
  } catch (error) {
    return jsonResponse({ error: error.message || "Invalid token" }, 401, request);
  }

  const address = normalizeAddress(payload.address || payload.sub);
  if (!address) {
    return jsonResponse({ error: "Token missing mailbox address" }, 401, request);
  }

  const mailbox = await loadMailbox(env, address);
  if (!mailbox) {
    return jsonResponse({ error: "Mailbox not found" }, 404, request);
  }

  if (isExpiredMailbox(mailbox)) {
    return jsonResponse({ error: "Mailbox expired" }, 410, request);
  }

  const url = new URL(request.url);
  const limit = clamp(parsePositiveInt(url.searchParams.get("limit"), 10), 1, 50);
  const offset = Math.max(0, parsePositiveInt(url.searchParams.get("offset"), 0));
  const results = (mailbox.messages || []).slice(offset, offset + limit);

  return jsonResponse(
    {
      address,
      total: (mailbox.messages || []).length,
      limit,
      offset,
      results,
    },
    200,
    request,
  );
}

async function handleEmail(message, env) {
  const configuredDomain = normalizeDomain(getConfig(env, "EMAIL_DOMAIN"));
  const to = normalizeAddress(message.to);

  if (!configuredDomain || !to || !to.endsWith(`@${configuredDomain}`)) {
    message.setReject("Unsupported recipient domain");
    return;
  }

  const mailbox = await loadMailbox(env, to);
  if (!mailbox) {
    message.setReject("Unknown recipient");
    return;
  }

  if (isExpiredMailbox(mailbox)) {
    message.setReject("Mailbox expired");
    return;
  }

  const rawText = await new Response(message.raw).text();
  const maxRawBytes = parsePositiveInt(getConfig(env, "MAX_RAW_BYTES"), CONFIG.MAX_RAW_BYTES);
  const storedRaw = truncateUtf8(rawText, maxRawBytes);
  const maxMessages = clamp(parsePositiveInt(getConfig(env, "MAX_MESSAGES"), CONFIG.MAX_MESSAGES), 1, 100);

  const mail = {
    id: buildMessageId(message),
    source: message.from || "",
    subject: message.headers.get("subject") || "",
    raw: storedRaw,
    receivedAt: new Date().toISOString(),
  };

  const nextMessages = [mail, ...(mailbox.messages || [])].slice(0, maxMessages);
  const nextMailbox = {
    ...mailbox,
    lastMessageAt: mail.receivedAt,
    messages: nextMessages,
  };

  await saveMailbox(env, nextMailbox);
  await updateMailboxIndex(env, nextMailbox);
}

function resolveLocalPart(body) {
  const rawName = String(body?.name || "").trim().toLowerCase();
  if (rawName) {
    return rawName;
  }
  return randomLocalPart();
}

function randomLocalPart() {
  const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  const values = new Uint8Array(12);
  crypto.getRandomValues(values);
  return Array.from(values, (value) => alphabet[value % alphabet.length]).join("");
}

function isValidAdminRequest(request, env) {
  const expected = String(env.ADMIN_PASSWORD || "");
  const provided = String(request.headers.get("x-admin-auth") || "");
  return expected.length > 0 && expected === provided;
}

async function loadMailbox(env, address) {
  const bucket = requireMailboxBinding(env);
  const object = await bucket.get(mailboxKey(address));
  if (!object) {
    return null;
  }
  const mailbox = await object.json();
  if (!mailbox || typeof mailbox !== "object") {
    return null;
  }
  if (isExpiredMailbox(mailbox)) {
    await deleteMailbox(env, address);
    return null;
  }
  return mailbox;
}

async function saveMailbox(env, mailbox) {
  const bucket = requireMailboxBinding(env);
  await bucket.put(mailboxKey(mailbox.address), JSON.stringify(mailbox), {
    httpMetadata: {
      contentType: "application/json; charset=utf-8",
    },
  });
}

function requireMailboxBinding(env) {
  const bucket = env?.[MAILBOX_BINDING];
  if (
    !bucket ||
    typeof bucket.get !== "function" ||
    typeof bucket.put !== "function" ||
    typeof bucket.delete !== "function"
  ) {
    throw new Error(`${MAILBOX_BINDING} R2 binding is missing`);
  }
  return bucket;
}

async function deleteMailbox(env, address) {
  const bucket = requireMailboxBinding(env);
  await bucket.delete(mailboxKey(address));
}

async function loadMailboxIndex(env) {
  const bucket = requireMailboxBinding(env);
  let object;
  try {
    object = await bucket.get(MAILBOX_INDEX_KEY);
  } catch {
    return rebuildMailboxIndex(env);
  }
  if (!object) {
    return rebuildMailboxIndex(env);
  }
  try {
    const data = await object.json();
    if (!Array.isArray(data)) {
      return rebuildMailboxIndex(env);
    }
    const items = data.filter((item) => item && typeof item === "object" && item.address);
    if (items.length > 0) {
      return items;
    }
    return rebuildMailboxIndex(env);
  } catch {
    return rebuildMailboxIndex(env);
  }
}

async function updateMailboxIndex(env, mailbox) {
  const bucket = requireMailboxBinding(env);
  const existing = await loadMailboxIndex(env);
  const next = [
    {
      address: mailbox.address,
      createdAt: mailbox.createdAt || "",
      expiresAt: mailbox.expiresAt || "",
      lastMessageAt: mailbox.lastMessageAt || "",
      messageCount: Array.isArray(mailbox.messages) ? mailbox.messages.length : 0,
    },
    ...existing.filter((item) => item.address !== mailbox.address),
  ].slice(0, parsePositiveInt(getConfig(env, "MAILBOX_INDEX_MAX"), CONFIG.MAILBOX_INDEX_MAX));

  await bucket.put(MAILBOX_INDEX_KEY, JSON.stringify(next), {
    httpMetadata: {
      contentType: "application/json; charset=utf-8",
    },
  });
}

async function rebuildMailboxIndex(env) {
  const bucket = requireMailboxBinding(env);
  let cursor = undefined;
  const objects = [];

  while (true) {
    const page = await bucket.list({
      prefix: MAILBOX_PREFIX,
      cursor,
      limit: 1000,
    });

    for (const object of page.objects || []) {
      objects.push(object);
    }

    if (!page.truncated) {
      break;
    }
    cursor = page.cursor;
  }

  objects.sort((left, right) => {
    const leftTs = left?.uploaded ? new Date(left.uploaded).getTime() : 0;
    const rightTs = right?.uploaded ? new Date(right.uploaded).getTime() : 0;
    return rightTs - leftTs;
  });

  const maxEntries = parsePositiveInt(getConfig(env, "MAILBOX_INDEX_MAX"), CONFIG.MAILBOX_INDEX_MAX);
  const selectedObjects = objects.slice(0, maxEntries);
  const items = selectedObjects
    .map((object) => ({
      address: addressFromMailboxKey(object.key),
      createdAt: object?.uploaded ? new Date(object.uploaded).toISOString() : "",
      expiresAt: "",
      lastMessageAt: "",
      messageCount: 0,
    }))
    .filter((item) => item.address);

  await bucket.put(MAILBOX_INDEX_KEY, JSON.stringify(items), {
    httpMetadata: {
      contentType: "application/json; charset=utf-8",
    },
  });
  return items;
}

async function searchMailboxEntriesByQuery(env, query, limit, seen = new Set()) {
  const bucket = requireMailboxBinding(env);
  let cursor = undefined;
  const items = [];

  while (items.length < limit) {
    const page = await bucket.list({
      prefix: MAILBOX_PREFIX,
      cursor,
      limit: 1000,
    });

    const matches = [];
    for (const object of page.objects || []) {
      const address = addressFromMailboxKey(object.key);
      if (!address || seen.has(address) || !matchesMailboxQuery({ address }, query)) {
        continue;
      }
      const mailbox = await loadMailboxSafely(env, address);
      if (!mailbox) {
        continue;
      }
      seen.add(address);
      matches.push(mailboxToIndexEntry(mailbox, object?.uploaded ? new Date(object.uploaded).toISOString() : ""));
    }

    matches.sort((left, right) => {
      const leftTs = Date.parse(String(left.createdAt || "")) || 0;
      const rightTs = Date.parse(String(right.createdAt || "")) || 0;
      return rightTs - leftTs;
    });

    items.push(...matches);

    if (!page.truncated) {
      break;
    }
    cursor = page.cursor;
  }

  return items.slice(0, limit);
}

async function resolveMailboxEntries(env, entries, limit) {
  const items = [];
  for (const entry of entries) {
    if (items.length >= limit) {
      break;
    }
    const address = normalizeAddress(entry?.address || "");
    if (!address) {
      continue;
    }
    const mailbox = await loadMailboxSafely(env, address);
    if (!mailbox) {
      continue;
    }
    items.push(mailboxToIndexEntry(mailbox, entry?.createdAt || ""));
  }
  return items;
}

async function loadMailboxSafely(env, address) {
  try {
    return await loadMailbox(env, address);
  } catch {
    return null;
  }
}

function mailboxToIndexEntry(mailbox, fallbackCreatedAt = "") {
  return {
    address: mailbox.address || "",
    createdAt: mailbox.createdAt || fallbackCreatedAt || "",
    expiresAt: mailbox.expiresAt || "",
    lastMessageAt: mailbox.lastMessageAt || "",
    messageCount: Array.isArray(mailbox.messages) ? mailbox.messages.length : 0,
  };
}

function mailboxKey(address) {
  return `${MAILBOX_PREFIX}${normalizeAddress(address)}`;
}

function addressFromMailboxKey(key) {
  const normalized = String(key || "");
  if (!normalized.startsWith(MAILBOX_PREFIX)) {
    return "";
  }
  return normalizeAddress(normalized.slice(MAILBOX_PREFIX.length));
}

async function issueMailboxJwt(env, address, now = Math.floor(Date.now() / 1000)) {
  return signJwt(
    {
      iss: "cf-mail-worker",
      sub: address,
      address,
      iat: now,
      exp: now + parsePositiveInt(getConfig(env, "JWT_TTL_SECONDS"), CONFIG.JWT_TTL_SECONDS),
    },
    getConfig(env, "JWT_SECRET"),
  );
}

function isExpiredMailbox(mailbox) {
  const expiresAtUnix = Number(mailbox?.expiresAtUnix || 0);
  return expiresAtUnix > 0 && expiresAtUnix <= Math.floor(Date.now() / 1000);
}

function normalizeDomain(value) {
  return String(value || "").trim().toLowerCase();
}

function normalizeAddress(value) {
  return String(value || "").trim().toLowerCase();
}

function isSafeLocalPart(value) {
  return /^[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9])?$/.test(value);
}

function matchesMailboxQuery(entry, query) {
  const address = normalizeAddress(entry?.address || "");
  if (!address || !query) {
    return false;
  }
  return address.includes(query);
}

function extractBearerToken(request) {
  const header = request.headers.get("authorization") || "";
  const match = header.match(/^Bearer\s+(.+)$/i);
  return match ? match[1] : null;
}

function buildMessageId(message) {
  return (
    message.headers.get("message-id") ||
    `${Date.now()}-${crypto.randomUUID()}`
  );
}

function parsePositiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

function getConfig(env, key) {
  const envValue = env?.[key];
  if (envValue !== undefined && envValue !== null && String(envValue) !== "") {
    return envValue;
  }
  return CONFIG[key];
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function truncateUtf8(value, maxBytes) {
  const encoder = new TextEncoder();
  const bytes = encoder.encode(String(value || ""));
  if (bytes.length <= maxBytes) {
    return value;
  }
  const slice = bytes.slice(0, maxBytes);
  return `${new TextDecoder().decode(slice)}\n...[truncated]`;
}

function jsonResponse(payload, status, request) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      ...JSON_HEADERS,
      ...corsHeaders(request),
    },
  });
}

function corsHeaders(request) {
  const origin = request?.headers?.get("origin") || "*";
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "authorization,content-type,x-admin-auth",
    vary: "Origin",
  };
}

async function signJwt(payload, secret) {
  if (!secret) {
    throw new Error("JWT_SECRET is not configured");
  }

  const header = { alg: "HS256", typ: "JWT" };
  const encodedHeader = base64UrlEncode(JSON.stringify(header));
  const encodedPayload = base64UrlEncode(JSON.stringify(payload));
  const signingInput = `${encodedHeader}.${encodedPayload}`;
  const signature = await signHmacSha256(signingInput, secret);
  return `${signingInput}.${signature}`;
}

async function verifyJwt(token, secret) {
  if (!secret) {
    throw new Error("JWT_SECRET is not configured");
  }

  const parts = String(token || "").split(".");
  if (parts.length !== 3) {
    throw new Error("Malformed token");
  }

  const [encodedHeader, encodedPayload, signature] = parts;
  const header = JSON.parse(base64UrlDecode(encodedHeader));
  if (header.alg !== "HS256") {
    throw new Error("Unsupported token algorithm");
  }

  const signingInput = `${encodedHeader}.${encodedPayload}`;
  const expectedSignature = await signHmacSha256(signingInput, secret);
  if (signature !== expectedSignature) {
    throw new Error("Invalid token signature");
  }

  const payload = JSON.parse(base64UrlDecode(encodedPayload));
  const now = Math.floor(Date.now() / 1000);
  if (payload.exp && Number(payload.exp) <= now) {
    throw new Error("Token expired");
  }

  return payload;
}

async function signHmacSha256(value, secret) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );

  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(value),
  );

  return base64UrlEncodeBytes(new Uint8Array(signature));
}

function base64UrlEncode(value) {
  return base64UrlEncodeBytes(new TextEncoder().encode(value));
}

function base64UrlEncodeBytes(bytes) {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlDecode(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  return atob(padded);
}
