# Fast Reference Video Generation — Design

## 1. Architecture Overview

```
ContentGenerationService (existing)
  ├── Queue (unified asyncio.Queue)
  ├── Worker Pool (gen_max_concurrency workers)
  │     └── _run_job() dispatches by function_mode:
  │           ├── function_mode != "fast_reference" → JimengClient (existing API path)
  │           └── function_mode == "fast_reference" → _run_fast_reference_job()
  │                 ├── browser_semaphore.acquire() [non-blocking tryout, re-enqueue if full]
  │                 ├── _acquire_account_atomic() [conditional UPDATE]
  │                 ├── FastReferenceBrowserExecutor.execute() [with asyncio.wait_for timeout]
  │                 └── FastReferencePoller (independent polling, not via jimeng_service)
  │
  ├── _polling_loop() (existing, for API-path jobs via jimeng_service)
  └── _fast_reference_polling_loop() (NEW, for fast_reference jobs via direct HTTP)
```

## 2. Key Design Decisions

### D-1: Worker Pool Strategy — Option C (Function Mode Router)
**Decision**: Keep unified queue but route by function_mode inside `_run_job()`.
**Why**: Avoids duplicating queue infrastructure. Fast_reference jobs that can't acquire browser_semaphore immediately will use `Semaphore.acquire()` with timeout — if timeout expires, re-enqueue the job with backoff.
**Trade-off**: Simpler than separate queues, but requires careful timeout to prevent worker starvation. Mitigated by ensuring `gen_max_concurrency > FAST_MAX_BROWSERS + 2`.

### D-2: Browser Lifecycle — Per-Job Ephemeral
**Decision**: Each fast_reference job creates its own browser → context → page, closed in finally.
**Why**: Account isolation (different cookies/fingerprints), crash containment, simpler than pooling.
**Trade-off**: ~3-5s startup overhead per job. Acceptable for 30-120s video generation tasks.

### D-3: Account Lease — Atomic Conditional UPDATE with Pool Isolation
**Decision**: Add `fast_enabled` Boolean field to Account model. `_acquire_account(db, purpose)` filters by `fast_enabled=True` for fast_reference, `gen_enabled=True` for API. Lease via conditional UPDATE + rowcount check.
```sql
-- For fast_reference:
UPDATE accounts SET gen_locked_until=:lock_until, gen_last_used_at=:now
WHERE id = :candidate_id
  AND fast_enabled = 1
  AND session_id IS NOT NULL
  AND health_status = 'healthy'
  AND (gen_locked_until IS NULL OR gen_locked_until <= :now)
```
**Why**: User confirmed `one_time` strategy as default for fast_reference. Without pool isolation, `one_time` would disable `gen_enabled` and drain the API pool. `fast_enabled` ensures API accounts are never consumed by browser tasks.
**Trade-off**: One extra DB field + migration + frontend toggle. Minimal overhead for critical isolation.

### D-4: Polling Architecture — Separate Loop for Fast Reference
**Decision**: Add `_fast_reference_polling_loop()` that polls directly via HTTP (1e67 signature), independent of jimeng_service.
**Why**: Fast reference jobs bypass jimeng_service for submission (browser-based), so polling should also be independent. Avoids coupling to jimeng_service availability.

### D-5: Migration Safety — PRAGMA-First Pattern
**Decision**: Check `PRAGMA table_info` before ALTER TABLE; only catch specific duplicate-column errors.
**Why**: Bare try/except swallows real errors (database locked, syntax errors). Existing project pattern already uses PRAGMA checks (see `ensure_proxy_node_columns`).

### D-6: Frontend @mention Editor — Custom Textarea + Dropdown
**Decision**: Build a simple custom @mention editor using textarea + @trigger dropdown, no external library.
**Why**: User confirmed minimal dependency approach. Asset library is small (<50 items), so a simple dropdown is sufficient. Avoids react-mentions dependency and style adaptation overhead.
**Implementation**: On `@` keystroke, extract query after `@`, filter assets by name/alias, show positioned dropdown. On selection, replace `@query` with `@asset_name` in textarea value.

### D-7: State Management — @tanstack/react-query
**Decision**: Use `@tanstack/react-query` for server state management in FastReference.tsx.
**Why**: User confirmed. react-query provides automatic caching, polling (refetchInterval), mutation management, and optimistic updates. Asset CRUD + job polling benefit significantly from built-in cache invalidation.
**Trade-off**: New dependency (~40KB gzipped), style differs from ContentGeneration.tsx's local state. Acceptable since FastReference is a new page with more complex state needs.

### D-8: Account Pool Isolation — fast_enabled Field
**Decision**: Add `fast_enabled` Boolean field to Account model, separate from `gen_enabled`.
**Why**: User confirmed `one_time` account consumption strategy. Without isolation, `one_time` would set `gen_enabled=False` and drain the API pool. `fast_enabled` ensures the two pools are independent.
**Trade-off**: One extra field + migration + frontend toggle. Minimal overhead.

## 3. Data Flow

### 3.1 Job Submission Flow
```
Frontend POST /api/fast-reference/jobs
  → Create ContentGenerationJob(function_mode="fast_reference", status="queued")
  → Resolve @mentions → Create ContentJobReference records
  → Enqueue job_id to ContentGenerationService.queue
  → Return job to frontend
```

### 3.2 Browser Execution Flow
```
Worker picks job from queue
  → Check function_mode == "fast_reference"
  → Acquire browser_semaphore (timeout=30s, re-enqueue if timeout)
  → Acquire account (atomic conditional UPDATE)
  → asyncio.wait_for(executor.execute(), timeout=FAST_TASK_TIMEOUT)
    → Launch Patchright browser
    → Inject sessionid cookie
    → Navigate to Dreamina video generate page
    → Dismiss error modals
    → Upload reference assets
    → Fill prompt
    → Setup network interceptor
    → Click generate
    → Wait for interceptor to capture history_record_id
  → Update job: status=submitted, remote_history_id=captured_id
  → Release browser (finally: close page/context/browser)
  → Release browser_semaphore
```

### 3.3 Polling Flow
```
_fast_reference_polling_loop() (every FAST_POLL_INTERVAL seconds)
  → SELECT jobs WHERE function_mode="fast_reference" AND status IN ("submitted","processing")
  → For each job (respecting poll interval):
    → Build API URL with region params
    → Sign with 1e67 algorithm
    → POST /mweb/v1/get_history_by_ids {history_ids: [id], submit_ids: [id]}
    → Parse response:
      → finish_time != 0 AND item_list present → Extract video_url → Download → Mark success
      → status in FAILED → Mark failed
      → Otherwise → Continue polling
    → On failure: try next region (TW → HK → TH)
    → On timeout (submitted_at + FAST_TASK_TIMEOUT): Mark failed
```

## 4. File Changes Matrix

### New Files (7)

| File | Language | Responsibility |
|------|----------|---------------|
| `backend/app/models/reference_asset.py` | Python | ReferenceAsset + ContentJobReference models |
| `backend/app/services/fast_reference_executor.py` | Python | Browser automation executor |
| `backend/app/services/fast_reference_poller.py` | Python | Direct HTTP polling with dual signature |
| `backend/app/services/reference_asset_service.py` | Python | Asset CRUD + @mention resolution |
| `backend/app/api/routers/fast_reference.py` | Python | Job + Asset REST API endpoints |
| `frontend/src/pages/FastReference.tsx` | TypeScript | Frontend page with react-query |
| `frontend/src/components/MentionInput.tsx` | TypeScript | Custom @mention textarea + dropdown component |

### Modified Files (8)

| File | Changes |
|------|---------|
| `backend/app/models/__init__.py` | Register ReferenceAsset, ContentJobReference |
| `backend/app/models/account.py` | Add `fast_enabled` Boolean field |
| `backend/app/services/content_generation.py` | Add function_mode dispatch, browser_semaphore, fast_reference polling loop, purpose-based account acquire |
| `backend/app/services/db_migration.py` | Add `ensure_fast_reference_tables()`, `ensure_fast_reference_fields()`, `ensure_accounts_fast_enabled()` |
| `backend/app/main.py` | Register new router, call new migration functions |
| `backend/app/core/config.py` | Add 8 new FAST_* settings |
| `frontend/src/config/routes.tsx` | Register /fast-reference route |
| `frontend/src/services/api.ts` | Add fastReferenceApi client + types |

## 5. API Contract

### Job Endpoints (prefix: `/api/fast-reference`)

| Method | Path | Request | Response |
|--------|------|---------|----------|
| POST | `/jobs` | `{prompt, model?, duration?, resolution?, ratio?}` | `ContentGenerationJob` |
| GET | `/jobs` | `?status=&page=&page_size=` | `PaginatedResponse<ContentGenerationJob>` |
| GET | `/jobs/{id}` | - | `ContentGenerationJob` |
| POST | `/jobs/{id}/retry` | - | `ContentGenerationJob` |
| DELETE | `/jobs/{id}` | - | `{success: true}` |

### Asset Endpoints (prefix: `/api/fast-reference/assets`)

| Method | Path | Request | Response |
|--------|------|---------|----------|
| GET | `/` | `?search=&asset_type=` | `ReferenceAsset[]` |
| POST | `/` | multipart: file + name + alias? + tags? | `ReferenceAsset` |
| PUT | `/{id}` | multipart: file? + name? + alias? | `ReferenceAsset` |
| DELETE | `/{id}` | - | `{success: true}` |
| POST | `/resolve` | `{prompt: string}` | `{mentions: [{name, asset_id, file_path}], missing: [string]}` |
