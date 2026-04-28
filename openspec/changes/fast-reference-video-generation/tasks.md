# Fast Reference Video Generation — Implementation Tasks (v2)

## Confirmed Decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Service architecture | Independent FastReferenceService + AccountLeaseService | Avoid God Service; ContentGenerationService stays unchanged |
| 2 | Worker pool | Separate fast_queue + fast_workers | Maximum isolation from API path |
| 3 | Account lease | Atomic conditional UPDATE + gen_lock_job_id owner verification | Prevent lock misrelease |
| 4 | Signature strategy | Dual: 11ac via jimeng_service → 1e67 direct HTTP fallback | Maximum availability |
| 5 | State management | @tanstack/react-query | Auto-caching, polling, mutation management |
| 6 | @mention editor | Custom textarea + @trigger dropdown with thumbnails | Minimal dependency |
| 7 | Asset library UI | Sheet (Radix Dialog + CSS transforms) | Reuse existing Radix dependency |
| 8 | Account pool | fast_enabled field, isolated from gen_enabled | one_time strategy must not drain API pool |
| 9 | Ambiguous submission | Mark failed, no auto-retry | Prevent duplicate charges |

## Phase 1: Data Layer (Models + Migration + Config)

- [x] 1.1 Create `backend/app/models/reference_asset.py` with ReferenceAsset model (id, name UNIQUE, alias, asset_type, file_path, file_url, thumbnail_path, file_size, sha256, mime_type, description, tags, usage_count, created_at, updated_at) and ContentJobReference model (id, job_id FK, asset_id FK, position, UNIQUE(job_id,position))
- [x] 1.2 Register new models in `backend/app/models/__init__.py`: import and add to __all__
- [x] 1.3 Add `fast_enabled = Column(Boolean, default=False)` and `gen_lock_job_id = Column(Integer, nullable=True)` to `backend/app/models/account.py`
- [x] 1.4 Add fast_reference columns to `backend/app/models/content_generation_job.py`: retry_count(Integer DEFAULT 0), max_retry(Integer DEFAULT 10), video_url(String 1024), browser_session_log(Text), polling_region(String 20), browser_started_at(DateTime), browser_finished_at(DateTime)
- [x] 1.5 Add `ensure_fast_reference_tables()` to `backend/app/services/db_migration.py`: CREATE TABLE IF NOT EXISTS for reference_assets and content_job_references with indexes
- [x] 1.6 Add `ensure_fast_reference_fields()` to `backend/app/services/db_migration.py`: PRAGMA table_info check then ALTER TABLE for content_generation_jobs new columns
- [x] 1.7 Add `ensure_accounts_fast_enabled()` to `backend/app/services/db_migration.py`: ALTER TABLE accounts ADD COLUMN fast_enabled BOOLEAN DEFAULT 0
- [x] 1.8 Add `ensure_accounts_lock_job_id()` to `backend/app/services/db_migration.py`: ALTER TABLE accounts ADD COLUMN gen_lock_job_id INTEGER
- [x] 1.9 Add 8 new settings to `backend/app/core/config.py` Settings class: fast_max_browsers(3), fast_account_strategy("one_time"), fast_credit_threshold(10), fast_max_retry(10), fast_poll_interval(5), fast_task_timeout(300), fast_headless(True), fast_assets_dir("data/fast_reference/assets")
- [x] 1.10 Call all new ensure_* functions in `backend/app/main.py` lifespan, after existing migration calls
- [x] 1.11 Update `backend/app/schemas/__init__.py`: add fast_enabled and gen_lock_job_id to AccountResponse; add FastReferenceJobRequest, ReferenceAssetResponse schemas

## Phase 2: Account Lease Service (Shared)

- [x] 2.1 Create `backend/app/services/account_lease_service.py` with AccountLeaseService class
- [x] 2.2 Implement `acquire(db, purpose, job_id, lease_seconds=600)`: candidate SELECT (limit 20) + conditional UPDATE + rowcount check; set gen_locked_until + gen_last_used_at + gen_lock_job_id
- [x] 2.3 Implement `release(db, account_id, job_id)`: UPDATE SET gen_locked_until=NULL, gen_lock_job_id=NULL WHERE id=account_id AND gen_lock_job_id=job_id
- [x] 2.4 Implement `extend(db, account_id, job_id, seconds=600)`: UPDATE SET gen_locked_until=now+seconds WHERE id=account_id AND gen_lock_job_id=job_id
- [x] 2.5 Refactor `ContentGenerationService._acquire_account()` to use `AccountLeaseService.acquire(db, purpose="api", job_id=job_id)`; update lock release in finally and _release_account_lock to use AccountLeaseService.release()

## Phase 3: Reference Asset Service

- [x] 3.1 Create `backend/app/services/reference_asset_service.py` with ReferenceAssetService class: CRUD methods (list_assets, create_asset, update_asset, delete_asset), file storage to FAST_ASSETS_DIR with relative paths
- [x] 3.2 Implement @mention extraction: regex `r"@([A-Za-z0-9_\-一-鿿]+)"` returning list of mention names
- [x] 3.3 Implement mention resolution: exact name match first, then alias comma-split contains match; return list of (mention_name, asset_id, file_path) tuples and list of missing mention names
- [x] 3.4 Implement atomic usage_count increment: `UPDATE reference_assets SET usage_count = usage_count + 1 WHERE id = :id`

## Phase 4: Browser Executor

- [x] 4.1 Create `backend/app/services/fast_reference_executor.py` with FastReferenceBrowserExecutor class and FastReferenceResult dataclass (success, history_id, task_id, error, browser_session_log, submitted_evidence)
- [x] 4.2 Implement Cookie injection: `{name:"sessionid", value:session_id, domain:".capcut.com", path:"/", httpOnly:True, secure:True, sameSite:"None"}`
- [x] 4.3 Implement page navigation to `https://dreamina.capcut.com/ai-tool/video/generate` with wait_until="networkidle", timeout=30000; call BrowserStealth.dismiss_error_modal and HumanBehavior.close_popup_if_exists
- [x] 4.4 Implement network interceptor: page.on("response") matching `/mweb/v1/aigc_draft/generate`; extract data.history_record_id or data.aigc_data.task.submit_id
- [x] 4.5 Implement reference asset upload: wait_for_selector input[type=file], set_input_files with file paths, wait for upload preview
- [x] 4.6 Implement prompt fill: try multiple selectors (textarea[placeholder*="describe"], textarea[class*="prompt"], [contenteditable="true"][class*="prompt"]), use HumanBehavior.type_like_human
- [x] 4.7 Implement generate button click: try selectors (button:has-text("Generate"), button:has-text("生成"), button[class*="generate"]), verify enabled, use HumanBehavior.click_like_human
- [x] 4.8 Implement wait_for_task_id: poll captured_history_id every 500ms up to 15s timeout
- [x] 4.9 Implement execute() orchestration: create_context → inject_cookies → create_page → setup_interceptor → goto → dismiss_modals → upload → fill_prompt → click_generate → wait_for_task_id; wrap in asyncio.wait_for; finally close page/context/browser with asyncio.wait_for(close(), 5); capture screenshot on failure; detect submission evidence for ambiguous cases

## Phase 5: Video Poller

- [x] 5.1 Create `backend/app/services/fast_reference_poller.py` with FastReferencePoller class
- [x] 5.2 Implement 1e67 signature: `md5("9e2c|{pathname}|web|8.4.0|{device_time}||1e67")`
- [x] 5.3 Implement _build_api_url: base=`https://mweb-api-sg.capcut.com`, path=/mweb/v1/get_history_by_ids, params: aid=513641, device_platform=web, region, did=random 19-digit
- [x] 5.4 Implement poll_video_status: POST with {history_ids:[id], submit_ids:[id]}, headers include Cookie sessionid, sign headers, Origin/Referer dreamina.capcut.com; try 11ac primary then 1e67 fallback
- [x] 5.5 Implement _extract_result: check finish_time != 0, extract video_url from item_list[0].video.transcoded_video.origin.video_url (fallback: video.video_url)
- [x] 5.6 Implement region degradation: try account.region → TW → HK → TH; persist successful polling_region
- [x] 5.7 Implement video download: httpx GET video_url → save to data/outputs/job_{id}_0.mp4; extract first frame thumbnail via imageio+Pillow
- [x] 5.8 Add jimeng_service proxy endpoint for history polling (TypeScript): new route in `backend/jimeng_service/src/api/routes/` that accepts history_ids + session token, signs with 11ac, proxies to Dreamina API

## Phase 6: Fast Reference Service (Integration)

- [x] 6.1 Create `backend/app/services/fast_reference_service.py` with FastReferenceService class: fast_queue, fast_workers list, fast_polling_task, browser_semaphore
- [x] 6.2 Implement start(): create fast_workers (settings.fast_max_browsers tasks), create fast_polling_task
- [x] 6.3 Implement stop(): signal workers to exit, cancel fast_polling_task, await all
- [x] 6.4 Implement enqueue(job_id): put job_id into fast_queue
- [x] 6.5 Implement _fast_worker_loop(worker_id): dequeue from fast_queue, call _run_fast_job()
- [x] 6.6 Implement _run_fast_job(job_id, worker_id): acquire browser_semaphore → AccountLeaseService.acquire(purpose="fast_reference") → resolve @mentions → FastReferenceBrowserExecutor.execute() → update job submitted → handle errors/ambiguous; release semaphore and account in finally (only release lock if job terminal)
- [x] 6.7 Implement _fast_polling_loop(): select fast_reference jobs in submitted/processing; for each call FastReferencePoller.poll_video_status; on success download video and mark success and release lock; on timeout mark failed and release lock; extend lock for in-progress jobs
- [x] 6.8 Implement account consumption strategy in _handle_account_after_job(): one_time=set fast_enabled=False; reusable=release lock only; disable_on_low_credit=check credits then set fast_enabled=False if below threshold
- [x] 6.9 Implement startup stale job recovery: queued → re-enqueue; submitted/processing with history_id → continue polling; submitting without history_id older than timeout → mark failed
- [x] 6.10 Update ContentGenerationService._polling_loop() to exclude function_mode="fast_reference" jobs
- [x] 6.11 Start/stop FastReferenceService in `backend/app/main.py` lifespan (after ContentGenerationService)

## Phase 7: API Routes

- [x] 7.1 Create `backend/app/api/routers/fast_reference.py` with APIRouter prefix="/api/fast-reference"
- [x] 7.2 Implement POST /jobs: validate FastReferenceJobRequest, create ContentGenerationJob(function_mode="fast_reference"), resolve @mentions and create ContentJobReference records, enqueue to FastReferenceService, return job
- [x] 7.3 Implement GET /jobs: list jobs filtered by function_mode="fast_reference", support status filter and pagination
- [x] 7.4 Implement GET /jobs/{id}: return single job with expanded reference assets
- [x] 7.5 Implement POST /jobs/{id}/retry: reset job status to queued, increment retry_count, re-enqueue to FastReferenceService
- [x] 7.6 Implement DELETE /jobs/{id}: soft-cancel if queued/submitting, hard-delete if terminal state
- [x] 7.7 Implement asset CRUD endpoints: GET /assets (list+search), POST /assets (multipart upload), PUT /assets/{id} (update), DELETE /assets/{id} (with RESTRICT check on active jobs)
- [x] 7.8 Implement POST /assets/resolve: extract @mentions from prompt, resolve each, return matches and missing list
- [x] 7.9 Register router in `backend/app/api/routers/__init__.py` and `backend/app/main.py`
- [x] 7.10 Update frontend Account management page: add fast_enabled toggle in account table and batch toggle action

## Phase 8: Frontend — Dependencies & Infrastructure

- [x] 8.1 Install @tanstack/react-query: `npm install @tanstack/react-query`; add QueryClientProvider to App.tsx wrapping all routes
- [x] 8.2 Create `frontend/src/components/ui/sheet.tsx`: Sheet component built on @radix-ui/react-dialog with fixed right positioning and slide-in/out CSS transforms
- [x] 8.3 Add upload() method to `frontend/src/services/api.ts`: accepts FormData, does NOT set Content-Type header (browser auto-sets multipart boundary)
- [x] 8.4 Add fastReferenceApi to `frontend/src/services/api.ts`: createJob, listJobs, getJob, retryJob, deleteJob, listAssets, uploadAsset, updateAsset, deleteAsset, resolveMentions; add TypeScript interfaces (FastReferenceJob, ReferenceAsset, FastReferenceJobRequest)

## Phase 9: Frontend — Components & Page

- [x] 9.1 Create `frontend/src/components/MentionInput.tsx`: custom textarea with @trigger dropdown; on @ keystroke extract query, filter assets by name/alias, show positioned dropdown with asset thumbnails; on selection replace @query with @asset_name; support keyboard navigation (up/down/enter/escape)
- [x] 9.2 Create `frontend/src/components/FastReferenceAssetLibrary.tsx`: Sheet content with grid view of assets with thumbnails, drag-drop upload zone, inline name/alias editing, delete with confirmation; use useMutation for CRUD
- [x] 9.3 Create `frontend/src/pages/FastReference.tsx` main page: glass-morphism bottom panel (reuse CSS pattern from ContentGeneration.tsx), VirtuosoGrid for job cards, useQuery for job list with refetchInterval=4000, useQuery for asset list
- [x] 9.4 Implement bottom panel: MentionInput for prompt, model selector (default Seedance 2.0 Fast), duration selector (5s/10s), resolution selector (720p/1080p), ratio selector (1:1/16:9/9:16), asset library button (opens Sheet), generate button with useMutation
- [x] 9.5 Implement job card component: video player for success, progress indicator for processing, error message + retry button for failed, queued badge; reuse card styling from ContentGeneration.tsx
- [x] 9.6 Register route in `frontend/src/config/routes.tsx`: path="/fast-reference", icon=Clapperboard or Video, i18nKey="nav.fast_reference", section="Core"
- [x] 9.7 Add i18n translations in `frontend/src/translations/index.ts` for fast-reference page labels, status messages, asset library labels

## Phase 10: Integration Testing & Tuning

- [x] 10.1 Manual E2E test: create job via frontend → verify browser launches → verify task_id captured → verify polling → verify video downloaded → verify frontend shows success
- [x] 10.2 Concurrency test: submit FAST_MAX_BROWSERS+2 jobs simultaneously, verify semaphore limits browser count, verify excess jobs queue properly
- [x] 10.3 Failure test: test with expired session_id → verify account marked unhealthy; test with invalid asset → verify clear error message; test network timeout → verify retry behavior
- [x] 10.4 CSS selector validation: manually verify all Dreamina page selectors against current live page
- [x] 10.5 Account pool isolation test: verify one_time strategy sets fast_enabled=False (not gen_enabled=False); verify API pool unaffected
- [x] 10.6 Lock owner test: verify gen_lock_job_id is set on acquire, verified on release; simulate concurrent lease attempts
- [x] 10.7 Ambiguous submission test: simulate interceptor miss with submission evidence → verify job marked failed, account disabled for one_time
- [x] 10.8 Stale job recovery test: kill service mid-execution → restart → verify queued jobs re-enqueue, submitting jobs without history_id marked failed
