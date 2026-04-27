# Fast Reference Video Generation — Implementation Tasks

## Confirmed Decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | API routing | Separate `/api/fast-reference/jobs` endpoints | 3-step workflow (draft→prepare→start) doesn't fit existing generate endpoint |
| 2 | Signature strategy | Dual: jimeng_service 11ac → direct HTTP 1e67 fallback | Maximum availability; need new jimeng_service proxy endpoint |
| 3 | State management | `@tanstack/react-query` | Auto-caching, polling, mutation management for asset CRUD + job list |
| 4 | @mention editor | Custom textarea + @trigger dropdown | Minimal dependency; asset library small (<50 items) |
| 5 | Account pool | New `fast_enabled` field, isolated from `gen_enabled` | `one_time` strategy must not drain API pool |

## Phase 1: Data Layer (Models + Migration + Config)

- [x] 1.1 Create `backend/app/models/reference_asset.py` with `ReferenceAsset` model (id, name UNIQUE, alias, asset_type, file_path, file_url, thumbnail_path, file_size, sha256, mime_type, description, tags, usage_count, created_at, updated_at) and `ContentJobReference` model (id, job_id FK, asset_id FK, position, UNIQUE(job_id,position))
- [x] 1.2 Register new models in `backend/app/models/__init__.py`: import and add to `__all__`
- [x] 1.3 Add `fast_enabled = Column(Boolean, default=False, comment="快速参考生成池启用状态")` to `backend/app/models/account.py`
- [x] 1.4 Add `ensure_fast_reference_tables()` to `backend/app/services/db_migration.py`: CREATE TABLE IF NOT EXISTS for reference_assets and content_job_references; create indexes (name, asset_type, job_id, asset_id)
- [x] 1.5 Add `ensure_fast_reference_fields()` to `backend/app/services/db_migration.py`: PRAGMA table_info check then ALTER TABLE for content_generation_jobs new columns (retry_count INTEGER DEFAULT 0, max_retry INTEGER DEFAULT 10, video_url VARCHAR(1024), browser_session_log TEXT, polling_region VARCHAR(20), browser_started_at DATETIME, browser_finished_at DATETIME)
- [x] 1.6 Add `ensure_accounts_fast_enabled()` to `backend/app/services/db_migration.py`: ALTER TABLE accounts ADD COLUMN fast_enabled BOOLEAN DEFAULT 0
- [x] 1.7 Add 8 new settings to `backend/app/core/config.py` Settings class: fast_max_browsers(3), fast_account_strategy("one_time"), fast_credit_threshold(10), fast_max_retry(10), fast_poll_interval(5), fast_task_timeout(300), fast_headless(True), fast_assets_dir("data/fast_reference/assets")
- [x] 1.8 Call `ensure_fast_reference_tables()`, `ensure_fast_reference_fields()`, `ensure_accounts_fast_enabled()` in `backend/app/main.py` lifespan, after existing migration calls
- [x] 1.9 Update `backend/app/schemas/__init__.py`: add `fast_enabled` to AccountResponse and AccountDetail; add FastReferenceJobRequest, ReferenceAssetResponse schemas

## Phase 2: Reference Asset Service

- [x] 2.1 Create `backend/app/services/reference_asset_service.py` with ReferenceAssetService class: CRUD methods (list_assets, create_asset, update_asset, delete_asset), file storage to FAST_ASSETS_DIR with relative paths, thumbnail generation for images
- [x] 2.2 Implement @mention extraction: regex `r"@([A-Za-z0-9_\-一-鿿]+)"` returning list of mention names
- [x] 2.3 Implement mention resolution: exact name match first, then alias comma-split contains match; return list of (mention_name, asset_id, file_path) tuples and list of missing mention names
- [x] 2.4 Implement atomic usage_count increment: `UPDATE reference_assets SET usage_count = usage_count + 1 WHERE id = :id`

## Phase 3: Browser Executor

- [x] 3.1 Create `backend/app/services/fast_reference_executor.py` with FastReferenceBrowserExecutor class and FastReferenceResult dataclass (task_id, history_id, success, error, browser_session_log)
- [x] 3.2 Implement Cookie injection: add `{name:"sessionid", value:session_id, domain:".capcut.com", path:"/", httpOnly:True, secure:True, sameSite:"None"}` to context before navigation
- [x] 3.3 Implement page navigation to `https://dreamina.capcut.com/ai-tool/video/generate` with wait_until="networkidle", timeout=30000; call BrowserStealth.dismiss_error_modal and HumanBehavior.close_popup_if_exists after load
- [x] 3.4 Implement network interceptor: page.on("response") matching `/mweb/v1/aigc_draft/generate`; extract data.history_record_id or data.aigc_data.task.submit_id; store as captured_history_id
- [x] 3.5 Implement reference asset upload: wait_for_selector input[type=file], set_input_files with file paths, wait for upload preview element
- [x] 3.6 Implement prompt fill: try multiple selectors (textarea[placeholder*="describe"], textarea[class*="prompt"], [contenteditable="true"][class*="prompt"]), use HumanBehavior.type_like_human
- [x] 3.7 Implement generate button click: try selectors (button:has-text("Generate"), button:has-text("生成"), button[class*="generate"]), verify enabled, use HumanBehavior.click_like_human
- [x] 3.8 Implement wait_for_task_id: poll captured_history_id every 500ms up to 15s timeout
- [x] 3.9 Implement execute() orchestration: create_context → inject_cookies → create_page → setup_interceptor → goto → dismiss_modals → upload → fill_prompt → click_generate → wait_for_task_id; wrap in asyncio.wait_for(timeout=FAST_TASK_TIMEOUT); finally close page/context/browser; capture screenshot on failure

## Phase 4: Video Poller

- [x] 4.1 Create `backend/app/services/fast_reference_poller.py` with FastReferencePoller class
- [x] 4.2 Implement dual signature: primary 11ac via jimeng_service proxy endpoint, fallback 1e67 direct HTTP `md5("9e2c|{pathname}|web|8.4.0|{device_time}||1e67")`
- [x] 4.3 Implement _build_api_url: base=`https://mweb-api-sg.capcut.com`, path=/mweb/v1/get_history_by_ids, params: aid=513641, device_platform=web, region, did=random 19-digit, da_version=3.3.12, web_version=7.5.0
- [x] 4.4 Implement poll_video_status: POST with {history_ids:[id], submit_ids:[id]}, headers include Cookie sessionid, sign headers, Origin/Referer dreamina.capcut.com; parse response checking ret=="0" or status_code==0
- [x] 4.5 Implement _extract_result: check finish_time != 0, extract video_url from item_list[0].video.transcoded_video.origin.video_url (fallback: video.video_url)
- [x] 4.6 Implement region degradation: try TW → HK → TH on failure; implement connect timeout 3s, read timeout 8s
- [x] 4.7 Implement video download: httpx GET video_url → save to data/outputs/fast_reference/; relative path returned
- [x] 4.8 Add jimeng_service proxy endpoint for history polling (TypeScript): new route in `backend/jimeng_service/src/api/routes/` that accepts history_ids + session token, signs with 11ac, proxies to Dreamina API

## Phase 5: Service Integration

- [x] 5.1 Add `browser_semaphore = asyncio.Semaphore(settings.fast_max_browsers)` to ContentGenerationService.__init__
- [x] 5.2 Refactor `_acquire_account()` to `_acquire_account(db, purpose="api")`: purpose="fast_reference" filters `fast_enabled=True`; purpose="api" filters `gen_enabled=True`; use conditional UPDATE + rowcount check for atomicity
- [x] 5.3 Add function_mode dispatch in `_run_job()`: if job.function_mode == "fast_reference", call `_run_fast_reference_job(job_id, worker_id)` instead of existing JimengClient path; extract existing API logic into `_run_api_job()`
- [x] 5.4 Implement `_run_fast_reference_job()`: acquire browser_semaphore → acquire_account(purpose="fast_reference") → resolve @mentions → execute browser → update job submitted → handle errors; release semaphore and account lock in finally
- [x] 5.5 Add `_fast_reference_polling_loop()`: select fast_reference jobs in submitted/processing status; for each, call FastReferencePoller.poll_video_status with dual signature; on success download video and call _update_job_success; on timeout (submitted_at + fast_task_timeout) mark failed and release account
- [x] 5.6 Start `_fast_reference_polling_loop` as asyncio.Task in ContentGenerationService.start(); cancel in stop()
- [x] 5.7 Implement account consumption strategy in `_handle_account_after_job()`: one_time=set fast_enabled=False (NOT gen_enabled); reusable=release lock only; disable_on_low_credit=check credits then set fast_enabled=False if below threshold
- [x] 5.8 Add startup stale job cleanup: on service start, find fast_reference jobs stuck in submitting for > FAST_TASK_TIMEOUT, mark as failed

## Phase 6: API Routes

- [x] 6.1 Create `backend/app/api/routers/fast_reference.py` with APIRouter prefix="/api/fast-reference"
- [x] 6.2 Implement POST /jobs: validate FastReferenceJobRequest, create ContentGenerationJob(function_mode="fast_reference"), resolve @mentions and create ContentJobReference records, enqueue to service, return job
- [x] 6.3 Implement GET /jobs: list jobs filtered by function_mode="fast_reference", support status filter and pagination
- [x] 6.4 Implement GET /jobs/{id}: return single job with expanded reference assets
- [x] 6.5 Implement POST /jobs/{id}/retry: reset job status to queued, increment retry_count, re-enqueue
- [x] 6.6 Implement DELETE /jobs/{id}: soft-cancel if queued/submitting, hard-delete if terminal state
- [x] 6.7 Implement asset CRUD endpoints: GET /assets (list+search), POST /assets (multipart upload), PUT /assets/{id} (update), DELETE /assets/{id} (with RESTRICT check on active jobs)
- [x] 6.8 Implement POST /assets/resolve: extract @mentions from prompt, resolve each, return matches and missing list
- [x] 6.9 Register router in `backend/app/main.py`: `app.include_router(fast_reference_router)`
- [x] 6.10 Update frontend Account management page: add fast_enabled toggle in account table and batch toggle action

## Phase 7: Frontend Page

- [x] 7.1 Install `@tanstack/react-query` dependency: `npm install @tanstack/react-query`; add QueryClientProvider to App.tsx
- [x] 7.2 Add fastReferenceApi to `frontend/src/services/api.ts`: createJob, listJobs, getJob, retryJob, deleteJob, listAssets, uploadAsset, updateAsset, deleteAsset, resolveMentions; add TypeScript interfaces (FastReferenceJob, ReferenceAsset, FastReferenceJobRequest)
- [x] 7.3 Create `frontend/src/components/MentionInput.tsx`: custom textarea component with @trigger dropdown; on `@` keystroke extract query, filter assets by name/alias, show positioned dropdown below cursor; on selection replace @query with @asset_name
- [x] 7.4 Create `frontend/src/pages/FastReference.tsx` main page: glass-morphism bottom panel (reuse CSS pattern from ContentGeneration.tsx), VirtuosoGrid for job cards, useQuery for job list with refetchInterval=4000, useQuery for asset list
- [x] 7.5 Implement bottom panel: MentionInput for prompt, model selector (default Seedance 2.0 Fast), duration selector (5s/10s), resolution selector (720p/1080p), ratio selector (1:1/16:9/9:16), generate button with useMutation
- [x] 7.6 Implement asset library Sheet/Drawer: grid view of assets with thumbnails, drag-drop upload zone, name/alias editing, delete with confirmation; use useMutation for CRUD operations
- [x] 7.7 Implement job card component: video player for success, progress indicator for processing, error message + retry button for failed, queued badge
- [x] 7.8 Register route in `frontend/src/config/routes.tsx`: path="/fast-reference", icon=Zap, i18nKey="nav.fast_reference"
- [x] 7.9 Add i18n translations for fast-reference page labels and status messages

## Phase 8: Integration Testing & Tuning

- [ ] 8.1 Manual E2E test: create job via frontend → verify browser launches → verify task_id captured → verify polling → verify video downloaded → verify frontend shows success
- [ ] 8.2 Concurrency test: submit FAST_MAX_BROWSERS+2 jobs simultaneously, verify semaphore limits browser count, verify excess jobs queue properly
- [ ] 8.3 Failure test: test with expired session_id → verify account marked unhealthy; test with invalid asset → verify clear error message; test network timeout → verify retry behavior
- [ ] 8.4 CSS selector validation: manually verify all Dreamina page selectors (file upload input, prompt textarea, generate button) against current live page; document any needed updates
- [ ] 8.5 Account pool isolation test: verify one_time strategy sets fast_enabled=False (not gen_enabled=False); verify API pool unaffected
- [ ] 8.6 Dual signature test: verify 11ac via jimeng_service works; simulate jimeng_service down → verify 1e67 fallback succeeds
