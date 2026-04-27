# Fast Reference Video Generation — Specifications

## 1. Functional Requirements

### FR-1: Browser-Based Video Generation
- System SHALL submit video generation tasks via Patchright browser automation on Dreamina Web
- System SHALL inject account `session_id` as Cookie before navigation
- System SHALL intercept `/mweb/v1/aigc_draft/generate` response to capture `history_record_id`
- System SHALL support configurable models (default: `Dreamina Seedance 2.0 Fast`)

### FR-2: Dual-Signature Polling
- System SHALL poll video status via `GET /mweb/v1/get_history_by_ids`
- System SHALL use primary signature (11ac via jimeng_service) first, fallback to direct HTTP (1e67)
- Fallback SHALL trigger ONLY on: 401/403, 5xx, network error, timeout, response format error
- System SHALL NOT fallback when remote returns valid processing/submitted status
- System SHALL support region degradation: TW → HK → TH

### FR-3: Reference Asset Library
- System SHALL store reference assets in DB table `reference_assets` (not JSON files)
- System SHALL support @mention syntax in prompts: `@asset_name` resolves to file path
- System SHALL resolve mentions by: 1) exact name match, 2) alias contains match
- System SHALL support CRUD operations via REST API
- System SHALL track `usage_count` via atomic SQL increment

### FR-4: Account Management
- System SHALL reuse existing `Account` model, adding `fast_enabled` Boolean field for pool isolation
- System SHALL acquire accounts via `_acquire_account(db, purpose)`: purpose="fast_reference" filters `fast_enabled=True`; purpose="api" filters `gen_enabled=True`
- System SHALL implement atomic account lease via conditional UPDATE + rowcount check
- System SHALL support 3 consumption strategies: `reusable`, `one_time`, `disable_on_low_credit`
- `one_time` strategy SHALL set `fast_enabled=False` (not `gen_enabled=False`), preserving API pool
- System SHALL NOT treat jimeng_service unavailability as "insufficient credits"

### FR-5: Concurrency Control
- System SHALL limit browser instances via `asyncio.Semaphore(FAST_MAX_BROWSERS)`
- System SHALL NOT block normal API-path generation workers while waiting for browser slots
- System SHALL implement per-job browser lifecycle: create → execute → close in finally block
- System SHALL enforce global timeout on browser execution via `asyncio.wait_for`

### FR-6: Frontend Page
- System SHALL provide `/fast-reference` page with glass-morphism bottom panel
- System SHALL support @mention autocomplete via custom textarea + dropdown (no external library)
- System SHALL use `@tanstack/react-query` for server state management (job list, asset list)
- System SHALL display job grid with real-time status polling via react-query refetchInterval
- System SHALL support reference asset upload/management via side drawer

## 2. Non-Functional Requirements

### NFR-1: Performance
- Browser execution timeout: configurable, default 120s (`FAST_TASK_TIMEOUT`)
- Polling interval: 5s (`FAST_POLL_INTERVAL`)
- Max concurrent browsers: 3 (`FAST_MAX_BROWSERS`)
- Account lease duration: 10 minutes, renewable

### NFR-2: Reliability
- Browser crash recovery: finally block closes page/context/browser
- Stale job cleanup: startup scan for stuck `submitting` jobs older than `FAST_TASK_TIMEOUT`
- Network interceptor miss: enter `uncertain_submitted` state, query account history before retry
- Polling timeout: fail job after `FAST_TASK_TIMEOUT` seconds from `submitted_at`

### NFR-3: Data Integrity
- Account lease: atomic conditional UPDATE with `gen_locked_until <= now` in WHERE clause
- Asset usage_count: `UPDATE SET usage_count = usage_count + 1` (SQL atomic increment)
- Migration: PRAGMA table_info check before ALTER, explicit error handling (not bare try/except)
- Foreign keys: CASCADE on job deletion, RESTRICT on asset deletion

## 3. Constraints (Resolved)

| ID | Constraint | Value | Source |
|----|-----------|-------|--------|
| C-1 | Browser engine | Patchright (not Playwright) | Hard constraint |
| C-2 | ORM | SQLAlchemy 2.0 async | Hard constraint |
| C-3 | Scheduling | asyncio Semaphore + Worker pool | Hard constraint |
| C-4 | Model reuse | Extend Account + ContentGenerationJob | Hard constraint |
| C-5 | Signature salt (primary) | `11ac`, platform `7`, `uri.slice(-7)` | jimeng_service code |
| C-6 | Signature salt (fallback) | `1e67`, platform `web`, full pathname | ShukeAI reverse |
| C-7 | Cookie name | `sessionid` (lowercase) | ShukeAI code |
| C-8 | Cookie domain | `.capcut.com` | ShukeAI code |
| C-9 | Default model | `Dreamina Seedance 2.0 Fast` | User confirmed |
| C-10 | Account strategy | `one_time` (default for fast_reference) | User confirmed |
| C-11 | Account pool isolation | `fast_enabled` field, separate from `gen_enabled` | User confirmed |
| C-12 | Target URL | `https://dreamina.capcut.com/ai-tool/video/generate` | MIGRATION_GUIDE |
| C-13 | @mention editor | Custom textarea + dropdown, no external library | User confirmed |
| C-14 | State management | `@tanstack/react-query` for server state | User confirmed |

## 4. PBT Properties

### P-1: Account Lease Atomicity
- **Invariant**: At any point in time, at most one active job holds a lease on any given account
- **Falsification**: Spawn N concurrent workers, each attempting to lease accounts; verify no account.id appears in more than one active job simultaneously

### P-2: Browser Semaphore Bound
- **Invariant**: Number of concurrent browser instances never exceeds `FAST_MAX_BROWSERS`
- **Falsification**: Submit `FAST_MAX_BROWSERS + 5` jobs simultaneously; instrument Semaphore to track peak concurrent acquisitions

### P-3: Signature Fallback Idempotency
- **Invariant**: Polling the same task_id with either signature strategy returns the same final video_url
- **Falsification**: For completed tasks, poll via both 11ac and 1e67; compare extracted video_url

### P-4: Asset Reference Round-Trip
- **Invariant**: `extract_mentions(prompt)` → `resolve_mention(name)` → asset.file_path always resolves for valid @mentions
- **Falsification**: Generate random prompts with known @mentions; verify all resolve to existing assets

### P-5: Job State Monotonicity
- **Invariant**: Job status transitions are monotonic: queued → submitting → submitted → processing → success/failed
- **Falsification**: Log all status transitions; verify no backward transitions occur (except retry: failed → queued)

### P-6: Browser Resource Cleanup
- **Invariant**: After executor.execute() returns (success or failure), zero browser processes remain from that execution
- **Falsification**: Count Chrome processes before and after each execution; delta must be zero

### P-7: Account Pool Isolation
- **Invariant**: `one_time` strategy on fast_reference jobs sets `fast_enabled=False` but never modifies `gen_enabled`
- **Falsification**: Run N fast_reference jobs with one_time strategy; verify all accounts still have original `gen_enabled` value

### P-8: Lease Release Safety
- **Invariant**: A task's finally block only releases the lease it acquired, never a lease acquired by a different task
- **Falsification**: Record `gen_locked_until` at acquire time; in finally, verify current `gen_locked_until` matches before clearing
