# Fast Reference Video Generation — Design (v2)

## 1. Architecture Overview

```
FastReferenceService (NEW — independent service)
  ├── fast_queue (asyncio.Queue)
  ├── fast_workers (N tasks, default 3)
  │     └── _run_fast_job() per worker:
  │           ├── browser_semaphore.acquire()
  │           ├── AccountLeaseService.acquire(purpose="fast_reference")
  │           ├── ReferenceAssetService.resolve_mentions()
  │           ├── FastReferenceBrowserExecutor.execute()
  │           └── Update job status
  ├── fast_polling_task
  │     └── FastReferencePoller (dual signature: 11ac → 1e67)
  └── startup recovery (stale job scan)

ContentGenerationService (UNCHANGED — API path only)
  ├── api_queue + api_workers → JimengClient
  ├── _polling_loop() — excludes function_mode="fast_reference"
  └── Uses AccountLeaseService.acquire(purpose="api")

AccountLeaseService (NEW — shared utility)
  ├── acquire(db, purpose, job_id) → atomic conditional UPDATE + rowcount
  ├── release(db, account_id, job_id) → verify gen_lock_job_id before clear
  └── extend(db, account_id, job_id, seconds) → renew gen_locked_until
```

## 2. Key Design Decisions

### D-1: Service Architecture — Independent FastReferenceService
**Decision**: Extract fast_reference logic into a standalone `FastReferenceService`, not extend `ContentGenerationService`.
**Why**: ContentGenerationService is already 1000+ lines. Adding browser automation, dual-signature polling, asset resolution, and independent queue/workers would create a God Service. Separation keeps each service focused and testable.
**Trade-off**: Two service files to maintain, but clear boundaries. Shared account lease logic extracted to `AccountLeaseService`.

### D-2: Worker Pool — Separate fast_queue + fast_workers
**Decision**: FastReferenceService owns its own asyncio.Queue and worker tasks, completely independent from ContentGenerationService's queue.
**Why**: User confirmed. Browser-based jobs (30-120s each) must not block API-path jobs. Independent queues eliminate queue-head blocking entirely.
**Trade-off**: Slightly more infrastructure code, but maximum isolation.

### D-3: Account Lease — Atomic Conditional UPDATE with Owner Verification
**Decision**: Add `gen_lock_job_id` Integer field to Account. Lease via conditional UPDATE + rowcount. Release verifies gen_lock_job_id matches.
**Why**: Without owner verification, a task's finally block could release a lock that was already expired and re-acquired by another task. gen_lock_job_id prevents this.
**Implementation**:
```sql
-- Acquire
UPDATE accounts SET gen_locked_until=:lock_until, gen_last_used_at=:now, gen_lock_job_id=:job_id
WHERE id = :candidate_id
  AND fast_enabled = 1  -- or gen_enabled = 1 for API
  AND session_id IS NOT NULL
  AND health_status = 'healthy'
  AND (gen_locked_until IS NULL OR gen_locked_until <= :now)

-- Release (only if still owner)
UPDATE accounts SET gen_locked_until=NULL, gen_lock_job_id=NULL
WHERE id = :account_id AND gen_lock_job_id = :job_id
```

### D-4: Browser Lifecycle — Per-Job Ephemeral
**Decision**: Each fast_reference job creates its own browser → context → page, closed in finally with `asyncio.wait_for(close(), 5)`.
**Why**: Account isolation (different cookies/fingerprints), crash containment, simpler than pooling.
**Trade-off**: ~3-5s startup overhead per job. Acceptable for 30-120s video generation tasks.

### D-5: Polling Architecture — Separate Loop with Dual Signature
**Decision**: FastReferenceService runs `_fast_reference_polling_loop()` independently. Uses 11ac (jimeng_service proxy) primary, 1e67 (direct HTTP) fallback. Persists successful polling_region.
**Why**: Fast reference jobs bypass jimeng_service for submission (browser-based), so polling should also be independent. Region persistence avoids repeated degradation.

### D-6: Existing Polling Loop — Exclude Fast Reference
**Decision**: ContentGenerationService._polling_loop() adds WHERE filter: `function_mode IS NULL OR function_mode != 'fast_reference'`.
**Why**: Fast reference jobs are polled by FastReferenceService. Without exclusion, both services would poll the same jobs.

### D-7: Migration Safety — PRAGMA-First Pattern
**Decision**: Check PRAGMA table_info before ALTER TABLE. Existing project convention.
**Why**: Bare try/except swallows real errors. PRAGMA checks are explicit and safe.

### D-8: Frontend Sheet — Radix Dialog + CSS Transforms
**Decision**: Build Sheet component from existing @radix-ui/react-dialog with slide-in CSS transforms. No new library.
**Why**: Project already has @radix-ui/react-dialog. A Sheet is just a Dialog positioned at the edge with slide animation. Avoids adding vaul or other drawer libraries.

### D-9: Frontend State — @tanstack/react-query
**Decision**: Use react-query for FastReference.tsx. QueryClientProvider at App root. Existing pages unchanged.
**Why**: User confirmed. react-query provides automatic caching, refetchInterval polling, mutation management. Asset CRUD + job polling benefit significantly.

### D-10: @mention Editor — Custom Textarea + Dropdown
**Decision**: Custom textarea with @trigger dropdown showing asset thumbnails. No external library.
**Why**: Asset library is small (<50 items). Simple dropdown is sufficient. Thumbnails aid visual identification.

### D-11: Ambiguous Submission — No Auto-Retry
**Decision**: If browser submits but interceptor misses history_id, mark failed with "ambiguous_submission". Do not auto-retry.
**Why**: Auto-retry risks duplicate charges. one_time accounts should be disabled if submission evidence exists.

## 3. Data Flow

### 3.1 Job Submission Flow
```
Frontend POST /api/fast-reference/jobs
  → Create ContentGenerationJob(function_mode="fast_reference", status="queued")
  → Resolve @mentions → Create ContentJobReference records
  → FastReferenceService.enqueue(job_id)
  → Return job to frontend
```

### 3.2 Browser Execution Flow
```
fast_worker picks job from fast_queue
  → Acquire browser_semaphore
  → AccountLeaseService.acquire(purpose="fast_reference", job_id=job_id)
  → asyncio.wait_for(executor.execute(), timeout=FAST_TASK_TIMEOUT)
    → Launch Patchright browser
    → Inject sessionid cookie
    → Navigate to Dreamina video generate page
    → Dismiss error modals
    → Upload reference assets
    → Fill prompt (stripped of @mentions)
    → Setup network interceptor
    → Click generate
    → Wait for interceptor to capture history_record_id
  → Update job: status=submitted, remote_history_id=captured_id
  → Release browser (finally: close page/context/browser)
  → Release browser_semaphore
  → AccountLeaseService keeps lock (polling will release)
```

### 3.3 Polling Flow
```
_fast_reference_polling_loop() (every FAST_POLL_INTERVAL seconds)
  → SELECT jobs WHERE function_mode="fast_reference" AND status IN ("submitted","processing")
  → For each job:
    → Build API URL with region params
    → Sign with 11ac (primary) or 1e67 (fallback)
    → POST /mweb/v1/get_history_by_ids
    → Parse response:
      → finish_time != 0 → Extract video_url → Download → Mark success → Release lock
      → FAILED status → Mark failed → Release lock
      → Otherwise → Extend lock → Continue polling
    → On failure: try next region
    → On timeout: Mark failed → Release lock
```

## 4. File Changes Matrix

### New Files (8)

| File | Language | Responsibility |
|------|----------|---------------|
| `backend/app/models/reference_asset.py` | Python | ReferenceAsset + ContentJobReference models |
| `backend/app/services/fast_reference_service.py` | Python | Independent fast_queue, fast_workers, fast_polling, startup recovery |
| `backend/app/services/fast_reference_executor.py` | Python | Patchright browser automation executor |
| `backend/app/services/fast_reference_poller.py` | Python | Direct HTTP polling with dual signature + region degradation |
| `backend/app/services/reference_asset_service.py` | Python | Asset CRUD + @mention resolution |
| `backend/app/services/account_lease_service.py` | Python | Shared atomic account lease (acquire/release/extend) |
| `backend/app/api/routers/fast_reference.py` | Python | Job + Asset REST API endpoints |
| `frontend/src/pages/FastReference.tsx` | TypeScript | Frontend page with react-query |

### New Frontend Files (3)

| File | Language | Responsibility |
|------|----------|---------------|
| `frontend/src/components/ui/sheet.tsx` | TypeScript | Sheet component (Radix Dialog + slide-in) |
| `frontend/src/components/MentionInput.tsx` | TypeScript | Custom @mention textarea + dropdown |
| `frontend/src/components/FastReferenceAssetLibrary.tsx` | TypeScript | Asset library Sheet content |

### Modified Files (10)

| File | Changes |
|------|---------|
| `backend/app/models/__init__.py` | Register ReferenceAsset, ContentJobReference |
| `backend/app/models/account.py` | Add `fast_enabled`, `gen_lock_job_id` fields |
| `backend/app/models/content_generation_job.py` | Add fast_reference columns (retry_count, video_url, browser_session_log, polling_region, browser_started_at, browser_finished_at) |
| `backend/app/services/content_generation.py` | Replace _acquire_account with AccountLeaseService; exclude fast_reference from _polling_loop |
| `backend/app/services/db_migration.py` | Add ensure_fast_reference_tables(), ensure_fast_reference_fields(), ensure_accounts_fast_enabled(), ensure_accounts_lock_job_id() |
| `backend/app/core/config.py` | Add 8 FAST_* settings |
| `backend/app/main.py` | Register fast_reference router, call new migrations, start/stop FastReferenceService |
| `backend/app/schemas/__init__.py` | Add fast_enabled/gen_lock_job_id to AccountResponse; add FastReferenceJobRequest, ReferenceAssetResponse |
| `frontend/src/config/routes.tsx` | Register /fast-reference route |
| `frontend/src/services/api.ts` | Add fastReferenceApi + upload() method + TypeScript interfaces |

### Also Modified

| File | Changes |
|------|---------|
| `frontend/src/translations/index.ts` | Add fast-reference i18n keys |
| `frontend/src/pages/Accounts.tsx` | Add fast_enabled toggle in account table |
| `backend/app/api/routers/__init__.py` | Export fast_reference router |

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
