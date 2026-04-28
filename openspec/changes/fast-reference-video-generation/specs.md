# Fast Reference Video Generation — Specifications (v2)

## 1. Functional Requirements

### FR-1: Browser-Based Video Generation
- System SHALL submit video generation tasks via Patchright browser automation on Dreamina Web
- System SHALL inject account `session_id` as Cookie (`sessionid`, domain `.capcut.com`) before navigation
- System SHALL intercept `/mweb/v1/aigc_draft/generate` response to capture `history_record_id` (fallback: `task.submit_id`)
- System SHALL support configurable models (default: `Dreamina Seedance 2.0 Fast`)
- System SHALL navigate to `https://dreamina.capcut.com/ai-tool/video/generate`

### FR-2: Dual-Signature Polling
- System SHALL poll video status via `POST /mweb/v1/get_history_by_ids`
- System SHALL use primary signature (11ac via jimeng_service proxy) first, fallback to direct HTTP (1e67)
- 1e67 signature: `md5("9e2c|{pathname}|web|8.4.0|{device_time}||1e67")`
- Fallback SHALL trigger ONLY on: 401/403, 5xx, network error, timeout, response format error
- System SHALL support region degradation: account.region → TW → HK → TH
- System SHALL persist successful `polling_region` for future priority

### FR-3: Reference Asset Library
- System SHALL store reference assets in DB table `reference_assets`
- System SHALL support @mention syntax in prompts: `@asset_name` resolves to file path
- System SHALL resolve mentions by: 1) exact name match, 2) alias comma-split contains match
- System SHALL support CRUD operations via REST API (including multipart file upload)
- System SHALL track `usage_count` via atomic SQL increment

### FR-4: Account Management
- System SHALL add `fast_enabled` Boolean field to Account model for pool isolation
- System SHALL add `gen_lock_job_id` Integer field to Account model for lock owner verification
- System SHALL acquire accounts via `AccountLeaseService.acquire(db, purpose)`: purpose="fast_reference" filters `fast_enabled=True`; purpose="api" filters `gen_enabled=True`
- System SHALL implement atomic account lease via conditional UPDATE + rowcount check + gen_lock_job_id owner
- System SHALL support 3 consumption strategies: `reusable`, `one_time`, `disable_on_low_credit`
- `one_time` strategy SHALL set `fast_enabled=False` (not `gen_enabled=False`)
- Lock release SHALL verify `gen_lock_job_id` matches before clearing

### FR-5: Concurrency Control
- System SHALL use independent `FastReferenceService` with separate fast_queue + fast_workers
- System SHALL limit browser instances via `asyncio.Semaphore(FAST_MAX_BROWSERS)`
- System SHALL NOT block normal API-path generation workers
- System SHALL implement per-job browser lifecycle: create → execute → close in finally block
- System SHALL enforce global timeout via `asyncio.wait_for(timeout=FAST_TASK_TIMEOUT)`
- Existing `_polling_loop()` SHALL exclude `function_mode="fast_reference"` jobs

### FR-6: Frontend Page
- System SHALL provide `/fast-reference` page with glass-morphism bottom panel
- System SHALL support @mention autocomplete via custom textarea + dropdown with asset thumbnails
- System SHALL use `@tanstack/react-query` for server state management
- System SHALL display job grid with real-time status polling via refetchInterval
- System SHALL support reference asset management via Sheet component (built on Radix Dialog + CSS transforms)

### FR-7: Ambiguous Submission Handling
- If browser submits but interceptor misses history_id, System SHALL mark job as `failed` with `ambiguous_submission` error
- System SHALL NOT auto-retry ambiguous submissions (risk of duplicate charges)
- If submission evidence exists (button loading, network 200), System SHALL set `fast_enabled=False` for one_time accounts

### FR-8: Stale Job Recovery
- On service start, System SHALL scan for stuck jobs:
  - `queued` → re-enqueue to fast_queue
  - `submitted/processing` with `remote_history_id` → continue polling
  - `submitting` without `remote_history_id` older than FAST_TASK_TIMEOUT → mark failed

## 2. Non-Functional Requirements

### NFR-1: Performance
- Browser execution timeout: configurable, default 300s (`FAST_TASK_TIMEOUT`)
- Polling interval: 5s (`FAST_POLL_INTERVAL`)
- Max concurrent browsers: 3 (`FAST_MAX_BROWSERS`)
- Account lease duration: 10 minutes, renewable via polling loop

### NFR-2: Reliability
- Browser crash recovery: finally block closes page/context/browser with `asyncio.wait_for(close(), 5)`
- Network interceptor miss: ambiguous_submission state, no auto-retry
- Polling timeout: fail job after FAST_TASK_TIMEOUT seconds from submitted_at

### NFR-3: Data Integrity
- Account lease: atomic conditional UPDATE with gen_locked_until + gen_lock_job_id in WHERE
- Asset usage_count: `UPDATE SET usage_count = usage_count + 1` (SQL atomic increment)
- Migration: PRAGMA table_info check before ALTER (existing project convention)
- Foreign keys: CASCADE on job deletion, RESTRICT on asset deletion

## 3. Constraints (Resolved)

| ID | Constraint | Value | Source |
|----|-----------|-------|--------|
| C-1 | Browser engine | Patchright (not Playwright) | Hard constraint |
| C-2 | ORM | SQLAlchemy 2.0 async | Hard constraint |
| C-3 | Scheduling | asyncio Semaphore + independent fast worker pool | User confirmed |
| C-4 | Model reuse | Extend Account + ContentGenerationJob | Hard constraint |
| C-5 | Signature salt (primary) | `11ac` via jimeng_service proxy | jimeng_service code |
| C-6 | Signature salt (fallback) | `1e67`, platform `web`, full pathname | ShukeAI reverse |
| C-7 | Cookie name | `sessionid` (lowercase) | ShukeAI code |
| C-8 | Cookie domain | `.capcut.com` | ShukeAI code |
| C-9 | Default model | `Dreamina Seedance 2.0 Fast` | User confirmed |
| C-10 | Account strategy | `one_time` (default) | User confirmed |
| C-11 | Account pool isolation | `fast_enabled` + `gen_lock_job_id` | User confirmed |
| C-12 | Target URL | `https://dreamina.capcut.com/ai-tool/video/generate` | MIGRATION_GUIDE |
| C-13 | @mention editor | Custom textarea + dropdown with thumbnails | User confirmed |
| C-14 | State management | `@tanstack/react-query` | User confirmed |
| C-15 | Asset library UI | Sheet (Radix Dialog + CSS transforms) | User confirmed |
| C-16 | Service architecture | Independent FastReferenceService + shared AccountLeaseService | User confirmed |
| C-17 | Worker pool | Separate fast_queue + fast_workers | User confirmed |

## 4. PBT Properties

### P-1: Account Lease Atomicity
- **Invariant**: At any point, at most one active job holds a lease on any given account
- **Falsification**: Spawn N concurrent workers, verify no account.id appears in more than one active job simultaneously; verify gen_lock_job_id matches

### P-2: Browser Semaphore Bound
- **Invariant**: Concurrent browser instances never exceed FAST_MAX_BROWSERS
- **Falsification**: Submit FAST_MAX_BROWSERS + 5 jobs; instrument Semaphore to track peak

### P-3: Signature Fallback Idempotency
- **Invariant**: Polling same task_id with either signature returns same final video_url
- **Falsification**: For completed tasks, poll via both 11ac and 1e67; compare video_url

### P-4: Asset Reference Round-Trip
- **Invariant**: extract_mentions(prompt) → resolve(name) → asset.file_path always resolves for valid @mentions
- **Falsification**: Generate prompts with known @mentions; verify all resolve

### P-5: Job State Monotonicity
- **Invariant**: Status transitions are monotonic: queued → submitting → submitted → processing → success/failed (except retry: failed → queued)
- **Falsification**: Log all transitions; verify no backward transitions

### P-6: Browser Resource Cleanup
- **Invariant**: After execute() returns, zero browser processes remain from that execution
- **Falsification**: Count Chrome processes before/after; delta must be zero

### P-7: Account Pool Isolation
- **Invariant**: one_time strategy sets fast_enabled=False but never modifies gen_enabled
- **Falsification**: Run N fast_reference jobs; verify all accounts retain original gen_enabled

### P-8: Lock Owner Safety
- **Invariant**: A task's finally block only releases the lease it acquired (gen_lock_job_id matches)
- **Falsification**: Record gen_lock_job_id at acquire; verify match before clearing
