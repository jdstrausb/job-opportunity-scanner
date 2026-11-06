---
title: "SvelteKit 5 Frontend & Co-Hosted FastAPI Bridge"
status: ready-for-agent
triage: ready-for-agent
date: 2026-11-06
disciplines:
  - domain-modeling
  - codebase-design
  - writing-for-agents
adrs:
  - docs/adr/0001-co-hosted-fastapi-and-static-sveltekit-spa.md
  - docs/adr/0002-persisted-match-explainability-and-startup-reevaluation.md
  - docs/adr/0003-comment-preserving-config-round-trip-and-hot-reload.md
  - docs/adr/0004-query-time-dismissal-filtering-and-alert-safe-ui-scans.md
solutions:
  - docs/solutions/001-atomic-digest-alert-retry-recovery.md
  - docs/solutions/002-sqlite-wal-idempotent-column-migrations.md
---

# Specification: SvelteKit 5 Frontend & Co-Hosted FastAPI Bridge

## Problem Statement

Operating and tuning the Job Opportunity Scanner currently requires manual command-line intervention, hand-editing a 350+ line `config.yaml` file and three `data/registries/*.json` catalogs, and reading raw terminal logs or digested SMTP emails:

1. **Opaque Job Triage & Missing Workflow State**: Users can only inspect matched jobs inside static email digests. There is no interactive triage feed to filter persisted jobs by Search Profile, remote eligibility, source, or Triage Status (`new | saved | applied`), nor is there an interactive breakdown showing *why* a posting matched or why a non-matching persisted posting fell short of a profile's gates or `min_affinity_score`.
2. **High-Friction Reactive Dismissals**: Muting a noisy job, company, or source requires copying a 64-character `job_key` or slug out of an email and invoking `python -m app.main --dismiss ...` in a terminal. Accidental dismissals cannot be undone with one click.
3. **Blind Configuration Tuning**: Adjusting `search_profiles` (required terms, OR keyword groups, exclusions, word-boundary terms, contextual terms, affinity weights, title boost multipliers) or Pre-Persistence Guardrails (`AgeFilter`, `LocationFilter`, `RoleFilter`, `DomainFilter`) requires editing YAML syntax by hand and restarting the daemon, with no dry-run feedback on how the changes affect already-collected jobs.
4. **Error-Prone Source & Dork Authoring**: Crafting `serpapi_google` search queries (`site:boards.greenhouse.io OR ...`) and toggling hundreds of companies across `data/registries/{greenhouse,lever,ashby}_companies.json` requires manual JSON/YAML editing. Furthermore, when Wildcard Sources (`identifier: "*"`) expand to 100+ companies, there is no visual dashboard rolling up per-ATS health or surfacing sanitized adapter errors.
5. **Invisible Pipeline & Scheduler Execution**: Users cannot see whether a scan is currently running, inspect the latest scan funnel metrics (fetched -> normalized -> filtered -> persisted -> matched -> notified), or trigger an on-demand scan without risking premature SMTP notifications or corrupting `alerts_sent` Digest Retry (`needs_alert`) state.

## Solution

Deliver a local-first web application by co-hosting a **FastAPI REST bridge** (`app/api/`) and a pre-built **SvelteKit 5 static Single-Page Application** (`frontend/` using `@sveltejs/adapter-static` and Svelte 5 runes) inside the existing `job-scanner` Python process on port `8000` (`UI_PORT` / `--port`):

1. **Co-Hosted Single-Process Runtime**: When launched in standard daemon mode, `job-scanner` starts FastAPI via Uvicorn and manages `SchedulerService` inside FastAPI's lifespan context manager, sharing `ScanPipeline._lock` and the SQLite WAL database in-process. Headless CLI flags (`--manual-run`, `--no-ui`, and `--dismiss`/`--undismiss`/`--list-dismissed`) preserve their exact existing behavior without starting a web server.
2. **Persisted Match Explainability & Triage Status**: Extend the `jobs` table with Match Explainability columns (`is_match`, `primary_profile`, `matched_profiles`, `affinity_score`, `score_breakdown`, `match_snippets`, `matched_fields`) and Triage Status (`user_status` defaulting to `'new'`). Reorder `ScanPipeline._process_source()` so `KeywordMatcher.evaluate()` executes before `JobRepository.upsert()`, preserve `user_status` across `content_hash` updates, and execute an in-process SQLite re-evaluation pass on startup (for legacy rows with `is_match IS NULL`) and whenever `search_profiles` are updated via the API.
3. **Master-Detail Job Triage Feed & 1-Click Reactive Dismissals**: Provide a keyboard-navigable triage workspace with instant filtering, sorting, "Why This Matched" score breakdowns, highlighted description snippets, `new | saved | applied` workflow pills, `alerted` vs. `pending_alert` badges, a 1-click Reactive Dismissal split-button (`job`, `company`, `source` scopes with quick-reason chips), a 5-second Undo toast, and a Dismissals Audit/Restore drawer backed by query-time filtering in `GET /api/jobs`.
4. **Visual Search Profiles Studio & Pre-Persistence Guardrails Editor**: Provide no-YAML editors for multi-track `search_profiles` and `filtering` rules with client- and server-side conflict validation, preset industry block packs, a **Zero-Network Config Preview** (`POST /api/config/preview`) against stored SQLite jobs, and **Config Round-Trip Hot-Reload** (`PUT /api/config`) powered by `ruamel.yaml` (preserving comments and unexpanded `identifier: "*"` wildcards, migrating legacy `search_criteria` to `search_profiles`, writing atomically via `.tmp` + `os.replace()`, and hot-reloading `pipeline.app_config`, `pipeline.keyword_matcher`, and `SchedulerService` under `ScanPipeline._lock`).
5. **Guided Source & Query Wizard + Wildcard Source Rollup**: Provide a client-side Visual Query Builder and bidirectional parser for `serpapi_google` queries with an opt-in live test action (`POST /api/sources/test-query`), an ATS Company Registry manager (`GET/PUT /api/registries/{ats_type}`, `POST /api/registries/{ats_type}/companies`), and a Source Health dashboard (`GET /api/sources`) that groups wildcard-expanded registry sources by ATS with drill-down support over the composite `(source_type, source_identifier)` health schema.
6. **Scan Control Center & Alert-Safe Scan Trigger**: Provide a live status bar and control drawer displaying `is_running`, `next_run_time`, and the `last_run_result` pipeline funnel visualization, paired with an **Alert-Safe Scan Trigger** (`POST /api/scan/trigger`, defaulting to `send_notifications=False`) that persists and matches jobs while leaving `alerts_sent` untouched so `SOL-001` Digest Retry (`needs_alert`) delivers those matches on the next scheduled digest run.

---

## User Stories

### Domain 1: Master-Detail Job Triage Feed
1. As a job seeker, I want to view a master-detail feed of persisted jobs showing only matched, non-dismissed jobs (`is_match = true`) by default, so that I can focus immediately on relevant opportunities.
2. As a job seeker, I want to toggle "Include Non-Matching Persisted Jobs" (`is_match = false`), so that I can inspect jobs that passed Pre-Persistence Guardrails but failed a Search Profile's keyword gates or `min_affinity_score`.
3. As a job seeker, I want to filter the triage feed by `primary_profile` (or any profile in `matched_profiles`), `source_type`, `source_identifier`, `is_remote`, `user_status` (`new`, `saved`, `applied`), `alert_status` (`alerted`, `pending_alert`), and free-text search across title, company, and description, so that I can slice my backlog by track or workflow stage.
4. As a job seeker, I want to sort the triage feed by `affinity_score` descending (default), `posted_at` descending, or `first_seen_at` descending with deterministic tie-breaking, so that the highest-affinity or freshest postings appear at the top.
5. As a job seeker, I want each job card in the master list to display the job title, company name, location badge (`Remote`, country, and secondary locations count), `primary_profile` pill, `affinity_score` badge, relative `posted_at` / `first_seen_at` timestamp, `user_status` indicator, and `Alert Status` badge (`alerted` with sent timestamp vs. `pending_alert`), so that I can scan key signals before opening the detail pane.
6. As a job seeker, I want the detail pane to display a "Why This Matched" Match Explainability card containing the winning `primary_profile`, all `matched_profiles`, the total `affinity_score`, per-term `score_breakdown` bars (showing base weight and whether the `title_boost_multiplier` applied), `matched_fields` tags (`title`, `description`, `location`), and extracted `match_snippets` with matched keywords highlighted, so that I understand why a role scored the way it did.
7. As a job seeker, I want to transition a job's Triage Status (`user_status`) between `new`, `saved`, and `applied` with a single click or keyboard shortcut, so that I can track my application progress directly in the scanner.
8. As a job seeker, I want a job's `user_status` (`saved` or `applied`) to be preserved automatically when an ATS updates the posting's description or location (`content_hash` change), so that I never lose my triage state when a company edits a live listing.
9. As a job seeker, I want a direct "Open Original Posting" external link on the detail pane, so that I can submit my application on the company's ATS board.

### Domain 2: 1-Click Reactive Dismissal Split-Button, Undo Toast & Audit/Restore Drawer
10. As a job seeker, I want a 1-click "Dismiss Job" primary button on every job card and detail pane that mutes the posting (`scope = "job"`, `item_key = job_key`) and immediately removes it from the active triage feed without deleting its row in `jobs`, so that I can clear irrelevant hits rapidly.
11. As a job seeker, I want a split-button dropdown alongside "Dismiss Job" offering "Mute Company (`company:<name>`)" and "Mute Source (`source:<identifier>`)" along with optional quick-reason chips (`"Wrong stack"`, `"Too senior"`, `"Comp/Location mismatch"`, `"Staffing agency"`, or custom text), so that I can permanently suppress noisy companies or boards in two clicks.
12. As a job seeker, I want muting a company or source to immediately hide all other jobs from that company or source in the current triage feed, so that the feed reflects the broader mute right away.
13. As a job seeker, I want a 5-second non-blocking Undo toast to appear after any dismissal (`job`, `company`, or `source`) that invokes `DELETE /api/dismissals` and restores the affected job(s) to their exact position and `user_status` in the feed if clicked, so that accidental mutes are painless to recover from.
14. As a job seeker, I want a Dismissals Audit & Restore drawer listing all active records in `dismissed_items` filterable by `scope` (`job`, `company`, `source`) and searchable by `item_key` or `reason`, showing `dismissed_at` timestamps and a "Restore" button per row, so that I can review and reverse historical mutes at any time.

### Domain 3: Visual Search Profiles Studio & Zero-Network Config Preview
15. As a job seeker, I want a Visual Search Profiles Studio that lists all configured `search_profiles` (or transparently shows the synthesized profile from legacy `search_criteria`) with enable/disable toggles, duplicate, reorder, create, and delete actions, so that I can manage multi-track job searches without editing YAML.
16. As a job seeker, I want interactive chip editors for `required_terms` (AND gate), `keyword_groups` (visual OR-group cards where each card holds interchangeable synonym chips), `exclude_terms` (whole-word NOT gate), `boundary_terms` (word-boundary regex enforcement), and `contextual_terms` (mapping a trigger term like `rails` to required context terms like `ruby`), so that I can express complex boolean matching rules visually.
17. As a job seeker, I want interactive controls for `affinity_weights` (term-to-weight sliders/inputs), `title_boost_multiplier` (`1.0` to `10.0`), and `min_affinity_score` (`>= 0.0`), so that I can tune how matching jobs are ranked.
18. As a job seeker, I want immediate inline validation when a term appears in both `required_terms` and `exclude_terms`, in both a `keyword_groups` entry and `exclude_terms`, or in both `affinity_weights` and `exclude_terms`, or when both `required_terms` and `keyword_groups` are empty, so that invalid profile rules are caught before saving.
19. As a job seeker, I want a "Dry-Run Preview against Stored Jobs" button (`POST /api/config/preview`) that evaluates my unsaved draft `search_profiles` against all non-dismissed jobs currently stored in SQLite with zero network calls, returning total matches, per-profile match counts, newly matched vs. dropped jobs compared to the current saved state, and top-scoring sample matches with their `score_breakdown`, so that I can verify my changes in under a second before saving.
20. As a job seeker, I want clicking "Save Profiles" (`PUT /api/config`) to perform a Config Round-Trip Hot-Reload that updates `config.yaml` via `ruamel.yaml` (preserving all YAML comments and migrating legacy `search_criteria` to `search_profiles`), hot-reloads `KeywordMatcher` under `ScanPipeline._lock`, and re-evaluates all stored SQLite jobs so the triage feed immediately reflects the new profile matches and scores.

### Domain 4: Pre-Persistence Guardrails Editor
21. As a job seeker, I want a Pre-Persistence Guardrails Editor for `filtering` rules organized into four clear sections corresponding to the pipeline's filter stages (`AgeFilter`, `LocationFilter`, `RoleFilter`, `DomainFilter`), so that I can control which jobs are rejected before database persistence.
22. As a job seeker, I want controls for `AgeFilter` (`max_job_age_days` between 1 and 730, `use_updated_at_fallback`, and `treat_recent_update_as_fresh`), so that stale postings are dropped early.
23. As a job seeker, I want controls for `LocationFilter` (`require_remote`, `require_remote_structured`, `remote_synonyms`, `acceptable_countries`, `remote_allowed_regions`, `remote_blocked_regions`, `allow_remote_canada_if_us_allowed`, `us_eligibility_phrases`, `allow_hybrid_cities`, and `remote_description_keywords`), so that geographic and remote-work eligibility is enforced consistently.
24. As a job seeker, I want controls for `RoleFilter` (`enable_seniority_filter`, `seniority_block_terms`, `seniority_allow_terms`, `enable_experience_filter`, `max_years_experience`, `hard_block_years` with validation that `hard_block_years >= max_years_experience`, `enable_role_filter`, `role_allow_terms`, and `role_block_terms`), so that roles outside my target seniority or discipline are filtered out.
25. As a job seeker, I want controls for `DomainFilter` (`enable_domain_filter`, `blocked_companies`, `blocked_domains`, and `domain_allow_terms`) plus 1-click **Preset Industry Block Packs** (e.g., `"Crypto / Web3"`, `"Sports Betting & iGaming"`, `"Defense & Clearance Required"`, `"Staffing & Body Shops"`) that merge curated terms into `blocked_domains` or `blocked_companies` without duplicating existing entries, so that I can block unwanted industries effortlessly.

### Domain 5: Guided Source & Query Wizard + Source Health Rollups
26. As a job seeker, I want a Source Health Dashboard (`GET /api/sources`) that displays explicit sources (`greenhouse`, `lever`, `ashby`, `rss`, `serpapi_google`) alongside **Wildcard Source Rollups** (`identifier: "*"`) grouped by ATS type, showing `total_enabled`, `healthy_count` (`last_success_at` present and `last_error_at` null), `error_count` (`last_error_at` present), and `unscanned_count`, so that 150+ registry-expanded companies do not flood the top-level view.
27. As a job seeker, I want to expand any Wildcard Source Rollup card to drill down into the constituent companies, filter by health state (`failing`, `healthy`, `unscanned`), and inspect sanitized `error_message` and `last_error_at` / `last_success_at` timestamps keyed by `(source_type, source_identifier)`, so that I can diagnose failing ATS slugs without exposing API keys.
28. As a job seeker, I want a Visual Query Builder for `serpapi_google` sources that lets me select target ATS domains (`boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, or custom domains), required exact phrases, OR keyword groups, excluded terms (`-term`), and `num_results`, and bi-directionally parses existing `serpapi_google` query strings into visual chips (falling back gracefully to raw query editing for arbitrary expressions), so that I never have to hand-write Google dork syntax.
29. As a job seeker, I want an opt-in "Test Query Live" button (`POST /api/sources/test-query`) in the `serpapi_google` wizard that executes a single dry-run fetch via `SerpApiSearchAdapter` (when `SERPAPI_API_KEY` is configured) and previews the hydrated `RawJob` titles, companies, locations, and URLs without persisting them or sending alerts, or displays a clear warning badge when `SERPAPI_API_KEY` is unset in the environment.
30. As a job seeker, I want an ATS Company Registry Manager (`GET /api/registries/{ats_type}`, `PUT /api/registries/{ats_type}`, `POST /api/registries/{ats_type}/companies`) that lets me search companies in `greenhouse_companies.json`, `lever_companies.json`, and `ashby_companies.json`, toggle individual companies `enabled: true/false`, add new `{identifier, name, enabled}` entries with duplicate-slug validation, and immediately re-expand wildcard sources in the live `AppConfig` under `ScanPipeline._lock`.

### Domain 6: Scan Control Center
31. As a job seeker, I want a persistent header indicator and Scan Control Center showing whether a scan is currently executing (`is_running`), the configured `scan_interval`, the `next_run_time` from `SchedulerService`, and a visual funnel of `last_run_result` (`total_fetched` -> `total_normalized` -> `total_filtered` with filter reason breakdown -> `total_upserted` -> `total_matched` -> `total_notified` / `alerts_sent` + `total_errors` and `total_duration_seconds`), so that I have full observability into pipeline throughput.
32. As a job seeker, I want an **Alert-Safe "Scan Now" Trigger** (`POST /api/scan/trigger`) that starts a background scan with `send_notifications: false` by default (with an explicit opt-in checkbox `"Also send email notifications now"`), returning `409 Conflict` if `ScanPipeline._lock` is already held, so that I can refresh jobs on demand from the UI without spamming my inbox or prematurely marking new matches as alerted in `alerts_sent`.

---

## Implementation Decisions

### 1. Co-Hosted Runtime, CLI Flags & Multi-Stage Docker Build (Q1 / ADR-0001)
- **Single-Process Co-Hosting**:
  - Introduce `app/api/` containing the FastAPI application factory `create_app(pipeline: ScanPipeline, scheduler_service: SchedulerService, config_manager: ConfigManager, static_dir: Optional[Path] = None) -> FastAPI`.
  - When `job-scanner` (`app/main.py`) runs in standard daemon mode (neither `--manual-run`, `--no-ui`, nor any `--dismiss`/`--undismiss`/`--list-dismissed` flag is passed), it boots Uvicorn serving the FastAPI app on host `0.0.0.0` (or `UI_HOST`, default `127.0.0.1` for local CLI and `0.0.0.0` in Docker) and port resolved by priority: `--port` CLI flag > `UI_PORT` environment variable > default `8000`.
  - `SchedulerService.start()` and `SchedulerService.shutdown(wait=False)` are managed inside the FastAPI `lifespan` async context manager so background scheduled scans run in-process alongside HTTP requests and share `ScanPipeline._lock`.
- **Headless CLI Preservation**:
  - `--manual-run`: Executes `pipeline.run_once()` synchronously and exits with code `0` or `1` without starting Uvicorn or `SchedulerService`.
  - `--no-ui`: Runs `SchedulerService` in headless daemon mode blocking on `shutdown_event.wait()`, identical to v1.0 behavior.
  - `--dismiss`, `--undismiss`, `--list-dismissed`: Handled immediately via `handle_dismissal_commands(args)` using `DATABASE_URL` directly without loading `config.yaml`, validating SMTP credentials, or starting FastAPI.
- **Static SPA Serving & Fallback**:
  - All API routes are mounted under `/api/*`.
  - If the compiled SvelteKit static bundle directory (`frontend/build`) exists, FastAPI mounts static assets (`/_app/*`, favicon, etc.) and serves `frontend/build/index.html` as a catch-all SPA fallback for non-`/api` GET requests so client-side navigation links resolve cleanly. If `frontend/build` is absent (e.g., backend-only test runs), `/api/*` routes still function and `/` returns a minimal JSON health payload `{"status": "ok", "ui_built": false}`.
- **Dependencies & Multi-Stage Dockerfile**:
  - Add `fastapi>=0.115.0`, `uvicorn>=0.32.0`, and `ruamel.yaml>=0.18.0` to `[project].dependencies` in `pyproject.toml`, and `httpx>=0.27.0` to `[project.optional-dependencies].dev` for `fastapi.testclient.TestClient`.
  - Update `Dockerfile` to add a `FROM node:22-slim AS frontend-builder` stage that runs `npm ci && npm run build` inside `frontend/` and copies `/frontend/build` into `/app/frontend/build` in the Python runtime stage, exposing port `8000` (`EXPOSE 8000`) and setting `UI_HOST=0.0.0.0`.

### 2. SQLite Schema Migration, Evaluation-Before-Upsert & Re-Evaluation Pass (Q2 / ADR-0002 / SOL-002)
- **Idempotent Column Additions on `jobs`**:
  - Extend `JobModel` (`app/persistence/schema.py`), `Job` (`app/domain/models.py`), and `_JOBS_MIGRATION_COLUMNS` with 8 columns:
    ```python
    _JOBS_MIGRATION_COLUMNS = {
        "secondary_locations": "JSON",
        "is_remote": "BOOLEAN",
        "location_country": "VARCHAR(255)",
        "is_match": "BOOLEAN",
        "primary_profile": "VARCHAR(255)",
        "matched_profiles": "JSON",
        "affinity_score": "FLOAT DEFAULT 0.0",
        "score_breakdown": "JSON",
        "match_snippets": "JSON",
        "matched_fields": "JSON",
        "user_status": "VARCHAR(50) DEFAULT 'new'",
    }
    ```
  - Following `SOL-002`, `_ensure_table_columns(engine)` inspects `jobs` after `Base.metadata.create_all(engine, checkfirst=True)` and executes `ALTER TABLE jobs ADD COLUMN ...` inside `with engine.begin() as conn:`.
  - Add composite/single indexes on `jobs` via `Index(..., if_not_exists=True)` or idempotent `CREATE INDEX IF NOT EXISTS` in `create_schema(engine)` for `idx_jobs_match_score` (`is_match`, `affinity_score`) and `idx_jobs_user_status` (`user_status`).
  - `Job.matched_fields` is serialized in JSON as `dict[str, list[str]]` (converting `MatchResult.matched_fields` sets to sorted lists).
- **Pipeline Ordering (`KeywordMatcher.evaluate()` Before `job_repo.upsert()`)**:
  - In `ScanPipeline._process_source()`, after a job passes `dismissed_repo.is_dismissed()` and `filtering_service.evaluate_job()`, run `match_result = self.keyword_matcher.evaluate(norm_result.job, norm_result.matchable_text)` **before** persisting.
  - Populate `norm_result.job` with the evaluation results:
    - `is_match = match_result.is_match`
    - `primary_profile = match_result.primary_profile`
    - `matched_profiles = list(match_result.matched_profiles)`
    - `affinity_score = match_result.affinity_score`
    - `score_breakdown = dict(match_result.score_breakdown)`
    - `match_snippets = list(match_result.snippets)`
    - `matched_fields = {k: sorted(v) for k, v in match_result.matched_fields.items() if v}`
  - When `norm_result.should_upsert` is `True`, call `job_repo.upsert(norm_result.job)`.
  - When `norm_result.should_upsert` is `False` (unchanged content), call `job_repo.update_last_seen_and_match(norm_result.job, scan_timestamp)` so `last_seen_at` and any match metadata are kept current without touching `first_seen_at` or `user_status`.
- **Preserving `user_status` Across `content_hash` Updates**:
  - In `JobRepository.upsert(job: Job)`, before merging, inspect if an existing `JobModel` row exists for `job.job_key`. If `existing` exists and `existing.user_status` is set (e.g., `'saved'` or `'applied'`), preserve `existing.user_status` unless explicitly overridden via `JobRepository.update_user_status(job_key, user_status)`. New rows default to `user_status = "new"`.
- **Startup & On-Save Re-Evaluation Pass**:
  - Provide `reevaluate_stored_jobs(session: Session, keyword_matcher: KeywordMatcher, *, only_unscored: bool = False) -> int`:
    - Queries rows from `jobs` (only rows where `is_match IS NULL` when `only_unscored=True`, or all rows when `only_unscored=False`).
    - Reconstructs `MatchableText` for each job using `JobNormalizer` text normalization helpers (`title_normalized`, `description_normalized`, `location_normalized`, `description_original`), runs `keyword_matcher.evaluate(job, matchable_text)`, and updates the 7 Match Explainability columns in batch while leaving `user_status`, `first_seen_at`, `last_seen_at`, and `content_hash` untouched.
  - Run `reevaluate_stored_jobs(..., only_unscored=True)` once during service startup after `init_database()` so pre-existing v1.0 databases populate Match Explainability immediately.
  - Run `reevaluate_stored_jobs(..., only_unscored=False)` whenever `search_profiles` are updated via `PUT /api/config`.

### 3. `ruamel.yaml` Config Round-Trip Hot-Reload (Q3 / ADR-0003)
- **`ConfigManager` Module (`app/config/manager.py`)**:
  - Uses `ruamel.yaml.YAML(typ="rt")` with `preserve_quotes = True` to load the raw `config.yaml` file into a comment-preserving `CommentedMap` AST **before** wildcard registry expansion.
  - Exposes `get_raw_config() -> AppConfig` (validated raw config where wildcard sources still have `identifier: "*"`) and `get_expanded_config() -> AppConfig` (with wildcards expanded via `CompanyRegistry`).
  - On `update_config(patch: ConfigUpdatePayload) -> AppConfig`:
    1. Loads the current `CommentedMap` from `config_path` (or initializes from the in-memory config if updating).
    2. Applies only the modified sections (`search_profiles`, `filtering`, `sources`, `scan_interval`, `email`, `logging`, `advanced`) to the `CommentedMap` in-place so comments on untouched sections and top-level headers remain intact.
    3. **Legacy `search_criteria` Migration**: When `search_profiles` is updated and the raw YAML document contains a legacy `search_criteria` key, deletes `search_criteria` from the `CommentedMap` and writes `search_profiles` so `config.yaml` transitions cleanly to multi-track format.
    4. **Wildcard Preservation**: Any source entry with `identifier: "*"` in `sources` is kept unexpanded in `config.yaml`; expanded registry companies are never written into `config.yaml`.
    5. Validates the candidate document via `AppConfig.model_validate(candidate_dict)` and `parse_duration(candidate.scan_interval)`. If validation fails, raises `ConfigurationError` (mapped to HTTP `422 Unprocessable Entity` with structured field errors) without touching disk or runtime state.
    6. **Atomic Disk Write**: Serializes the validated `CommentedMap` to `f"{config_path}.tmp"`, flushes and fsyncs, and executes `os.replace(tmp_path, config_path)`.
    7. **Lock-Synchronized Hot-Reload**: Acquires `pipeline._lock` (with a timeout; if a scan is currently running, waits up to 5 seconds or schedules swap immediately upon lock release, or returns `409 Conflict` if locked depending on operation, or swaps atomically via a dedicated config lock synchronized with `ScanPipeline`), expands wildcards into `expanded_config`, updates `pipeline.app_config = expanded_config` and `pipeline.keyword_matcher = KeywordMatcher(expanded_config.get_effective_profiles())`, reschedules `scheduler_service.reschedule(expanded_config.scan_interval_seconds)` if `scan_interval` changed, and runs `reevaluate_stored_jobs(session, pipeline.keyword_matcher, only_unscored=False)` if `search_profiles` changed.

### 4. Composite Source Health Key & Wildcard Source Rollups (Q4)
- **Composite Primary Key `(source_type, source_identifier)`**:
  - `SourceStatusModel` and `SourceRepository` (`app/persistence/schema.py`, `app/persistence/repositories.py`) use the composite primary key `(source_type, source_identifier)` and idempotent table-rebuild helper `_ensure_sources_primary_key(engine)` inside `create_schema(engine)`.
- **Wildcard Source Rollup in `GET /api/sources`**:
  - Cross-references raw configured sources (`config_manager.get_raw_config().sources`), registry companies (`CompanyRegistry`), and health rows (`SourceRepository.get_all()`):
  - Explicit sources (`identifier != "*"`) are returned in `explicit_sources[]` with their configured `name`, `type`, `identifier`, `enabled`, and latest `SourceStatus` (`last_success_at`, `last_error_at`, sanitized `error_message`, and derived `health_status: "healthy" | "error" | "unscanned" | "disabled"`).
  - Wildcard sources (`identifier == "*"`) are returned in `wildcard_rollups[]`, grouped by `type` (`greenhouse`, `lever`, `ashby`), containing:
    - `name`, `type`, `enabled`
    - `total_companies`, `total_enabled`, `healthy_count`, `error_count`, `unscanned_count`
    - `last_success_at` (max across group), `last_error_at` (max across group)
    - `companies[]` (drill-down list of `{identifier, name, enabled, health_status, last_success_at, last_error_at, error_message}` sorted with errors first, then alphabetically).

### 5. Query-Time Reactive Dismissal Filtering (Q5 / ADR-0004)
- **Non-Destructive Dismissal Filtering**:
  - Dismissing an item via `POST /api/dismissals` (`scope: "job" | "company" | "source"`, `item_key`, optional `reason`) upserts into `dismissed_items` via `DismissedItemRepository.dismiss()` and **never deletes rows from `jobs`**.
  - `JobRepository.query_jobs(filters: JobQueryFilters)` builds a single SQLAlchemy query on `JobModel` that excludes any row matching an active `DismissedItemModel` across all three scopes (unless `include_dismissed=True` is explicitly requested):
    - `func.lower(JobModel.job_key)` not in `SELECT item_key FROM dismissed_items WHERE scope = 'job'`
    - `func.lower(JobModel.company)` not in `SELECT item_key FROM dismissed_items WHERE scope = 'company'`
    - `func.lower(JobModel.source_identifier)` not in `SELECT item_key FROM dismissed_items WHERE scope = 'source'`
  - Calling `DELETE /api/dismissals` (`scope`, `item_key`) invokes `DismissedItemRepository.undismiss()`, immediately making all affected jobs visible again in `GET /api/jobs` with their original `user_status`, `first_seen_at`, and Match Explainability intact.

### 6. Alert-Safe Scan Trigger, Zero-Network Preview & Visual Query Builder (Q6 / ADR-0004 / SOL-001)
- **Alert-Safe Scan Trigger (`POST /api/scan/trigger`)**:
  - Extend `ScanPipeline.run_once(send_notifications: bool = True) -> PipelineRunResult` and track `self.last_run_result: Optional[PipelineRunResult] = None` and `self.is_running: bool` (derived from `self._lock.locked()`) on `ScanPipeline`.
  - When `send_notifications=False` (the default for `POST /api/scan/trigger`):
    - `_process_source()` fetches, normalizes, checks dismissals, runs `FilteringService`, runs `KeywordMatcher.evaluate()`, and upserts/updates rows in `jobs` and `sources` as normal, incrementing `stats.matched_count` for matches.
    - Notification dispatch (`send_digest()` and `send_notifications()`) and `alert_repo.record_alert()` are **skipped**.
    - Because no `alerts_sent` rows are written for newly discovered or updated matching jobs, their `Alert Status` is `pending_alert`, and on the next scheduled daemon scan (which calls `pipeline.run_once(send_notifications=True)`), `CandidateMatch.is_retry` (`SOL-001` `needs_alert`) automatically evaluates to `True` and includes them in the consolidated email digest.
  - `POST /api/scan/trigger` checks `pipeline.is_running`; if `True`, returns `409 Conflict`. Otherwise, dispatches `pipeline.run_once(send_notifications=payload.send_notifications)` in a background thread and returns `202 Accepted`.
- **Zero-Network Config Preview (`POST /api/config/preview`)**:
  - Accepts a candidate list of `SearchProfile` objects (or a single `SearchProfile`), validates them via Pydantic, instantiates a transient `KeywordMatcher(candidate_profiles)`, loads all non-dismissed `Job` rows from SQLite, evaluates each in-memory using `JobNormalizer` text normalization, and returns:
    - `total_evaluated`: count of stored non-dismissed jobs evaluated
    - `total_matched`: count of jobs matching at least one candidate profile
    - `delta_matched`: difference compared to current persisted `is_match == True` count (`newly_matched_count`, `dropped_match_count`)
    - `profile_counts`: `dict[str, int]` mapping each profile name to its match count
    - `sample_matches`: top 20 matching jobs ordered by `affinity_score` descending with `job_key`, `title`, `company`, `location`, `primary_profile`, `matched_profiles`, `affinity_score`, `score_breakdown`, and `match_snippets`.
- **Visual Query Builder & Live Test (`POST /api/sources/test-query`)**:
  - Client-side pure module `frontend/src/lib/query-builder.ts` converts structured query state (`sites: string[]`, `exactPhrases: string[]`, `orGroups: string[][]`, `excludedTerms: string[]`, `rawTail: string`) to a canonical Google search query string and parses existing query strings back into structured state.
  - `POST /api/sources/test-query` accepts `{"query": str, "num_results": int = 10}`. If `env_config.serpapi_api_key` is `None`, returns `400 Bad Request` with `{"detail": "SERPAPI_API_KEY is not configured in the environment", "serpapi_configured": false}`. Otherwise invokes `SerpApiSearchAdapter` for that single query without persisting or alerting, returning the list of discovered `RawJob` previews (`title`, `company`, `location`, `url`, `is_remote`, `hydrated: bool`).

### 7. FastAPI Route Contracts (`app/api/routes/`)

| Method & Path | Request Query / Body | Response Shape & Status Codes |
| :--- | :--- | :--- |
| `GET /api/jobs` | Query params: `match_mode` (`"matched"` default \| `"all"` \| `"unmatched"`), `profile` (`str`), `source_type` (`str`), `source_identifier` (`str`), `is_remote` (`bool`), `user_status` (`"new"` \| `"saved"` \| `"applied"`), `alert_status` (`"alerted"` \| `"pending_alert"`), `q` (`str`), `sort_by` (`"affinity_score"` default \| `"posted_at"` \| `"first_seen_at"`), `sort_order` (`"desc"` default \| `"asc"`), `limit` (`int`, default `50`, max `200`), `offset` (`int`, default `0`) | `200 OK`: `{items: JobTriageItem[], total: int, limit: int, offset: int, counts: {matched: int, saved: int, applied: int, pending_alert: int}}`. Each `JobTriageItem` includes job fields + Match Explainability + `user_status` + `alert_status: "alerted" \| "pending_alert" \| "none"` + `alert_sent_at: Optional[datetime]`. |
| `GET /api/jobs/{job_key}` | Path: `job_key` | `200 OK`: Full `JobTriageItem` including full `description` and `match_snippets`; `404 Not Found` if absent. |
| `PATCH /api/jobs/{job_key}/status` | Body: `{user_status: "new" \| "saved" \| "applied"}` | `200 OK`: Updated `JobTriageItem`; `404 Not Found` if `job_key` absent; `422` for invalid status. |
| `GET /api/dismissals` | Query param: optional `scope` (`"job"` \| `"company"` \| `"source"`), optional `q` (`str`) | `200 OK`: `{items: DismissedItemDTO[], total: int}`. |
| `POST /api/dismissals` | Body: `{scope: "job" \| "company" \| "source", item_key: str, reason?: str}` | `201 Created`: `{item: DismissedItemDTO, hidden_job_count: int}`. |
| `DELETE /api/dismissals` | Query params: `scope` (`"job"` \| `"company"` \| `"source"`), `item_key` (`str`) | `200 OK`: `{restored: bool, scope: str, item_key: str}`; `404 Not Found` if dismissal did not exist. |
| `GET /api/config` | None | `200 OK`: `{search_profiles: SearchProfile[], filtering: FilteringRules, sources: SourceConfig[], scan_interval: str, email: EmailConfig, logging: LoggingConfig, advanced: AdvancedConfig, env_status: {serpapi_configured: bool, smtp_configured: bool}}`. Note: `sources` returns the raw configured sources (preserving `identifier: "*"`). |
| `PUT /api/config` | Body: `ConfigUpdatePayload` (partial or full: `search_profiles?`, `filtering?`, `sources?`, `scan_interval?`, `email?`) | `200 OK`: Updated config response + `{reevaluated_jobs_count: int}` after `ruamel.yaml` round-trip write and hot-reload; `422 Unprocessable Entity` with structured field validation errors on invalid config. |
| `POST /api/config/preview` | Body: `{search_profiles: SearchProfile[]}` | `200 OK`: `{total_evaluated: int, total_matched: int, newly_matched_count: int, dropped_match_count: int, profile_counts: Record<string, int>, sample_matches: JobPreviewMatch[]}`; `422` if candidate profiles fail validation. |
| `GET /api/registries/{ats_type}` | Path: `ats_type` (`"greenhouse"` \| `"lever"` \| `"ashby"`), Query: optional `q` (`str`), `enabled_only` (`bool = false`) | `200 OK`: `{ats_type: str, version: str, updated_at: str, total: int, enabled_count: int, companies: RegistryCompany[]}`. |
| `PUT /api/registries/{ats_type}` | Body: `{companies: RegistryCompany[]}` (or bulk toggle patch `{updates: {identifier: str, enabled: bool}[]}`) | `200 OK`: Writes updated JSON to `data/registries/{ats_type}_companies.json` atomically, re-expands wildcard sources in `pipeline.app_config` under `ScanPipeline._lock`, and returns updated registry summary. |
| `POST /api/registries/{ats_type}/companies` | Body: `{identifier: str, name: str, enabled: bool = true}` | `201 Created`: Appends new company to registry JSON, re-expands wildcards, returns updated registry; `409 Conflict` if `identifier` already exists in that ATS registry. |
| `GET /api/sources` | None | `200 OK`: `{explicit_sources: ExplicitSourceHealth[], wildcard_rollups: WildcardRollupHealth[]}`. All `error_message` strings are passed through `BaseAdapter._sanitize_url()`. |
| `POST /api/sources/test-query` | Body: `{query: str, num_results: int = 10}` | `200 OK`: `{query: str, count: int, jobs: RawJobPreview[]}`; `400 Bad Request` if `SERPAPI_API_KEY` is unset; `502 Bad Gateway` with sanitized error on upstream SerpAPI failure. |
| `GET /api/scan/status` | None | `200 OK`: `{is_running: bool, scan_interval: str, scan_interval_seconds: int, next_run_time: Optional[datetime], last_run_result: Optional[PipelineRunResultDTO]}`. |
| `POST /api/scan/trigger` | Body: `{send_notifications: bool = False}` | `202 Accepted`: `{status: "started", send_notifications: bool}`; `409 Conflict` if `pipeline.is_running` is `True`. |

### 8. SvelteKit 5 Frontend Architecture (`frontend/`)
- **Build & Routing**:
  - SvelteKit 5 with TypeScript, Vite, Tailwind CSS, and `@sveltejs/adapter-static` configured with `fallback: "index.html"` (`ssr = false`).
  - Top-level navigation shell (`src/routes/+layout.svelte`) with persistent **Scan Control Bar** (live `is_running` pulse, `last_run_result` summary badge, and "Scan Now" split-trigger) and 4 primary workspace tabs:
    1. `/` (`Triage Feed`): Master-detail job list + filter/sort bar + "Why This Matched" explainability card + Triage Status pills + 1-click Reactive Dismissal split-button + 5-second Undo toast + slide-over Dismissals Audit/Restore drawer.
    2. `/profiles` (`Search Profiles Studio`): Multi-track profile list + chip editors for required/OR-groups/excluded/boundary/contextual terms + weight sliders & title boost + inline conflict validator + live Dry-Run Preview panel (`POST /api/config/preview`) + Save & Hot-Reload bar.
    3. `/guardrails` (`Pre-Persistence Guardrails`): 4-stage visual editor (`AgeFilter`, `LocationFilter`, `RoleFilter`, `DomainFilter`) + 1-click Preset Industry Block Packs + Save & Hot-Reload bar.
    4. `/sources` (`Sources, Query Wizard & Health`): Wildcard Source Rollup cards with expandable company health tables + Explicit Sources manager + Visual SerpAPI Query Builder with live preview + ATS Company Registry browser/editor.

---

## Testing Decisions

### What Makes a Good Test
Following the `codebase-design` discipline, tests must exercise modules strictly through their **public interfaces at the highest seam**, verifying observable outputs and state transitions rather than mocking internal private helpers. Every feature requirement above is verified across **4 confirmed public testing seams**:

### Seam 1: Persistence & Schema Migration Seam (`tests/test_persistence.py` & `tests/test_migrations.py`)
- **Interface Under Test**: `create_schema(engine)`, `JobRepository`, `SourceRepository`, `DismissedItemRepository`, `AlertRepository`, and `reevaluate_stored_jobs(session, keyword_matcher)`.
- **Behaviors Verified**:
  - Idempotent migration of legacy v1.0 SQLite databases adding all 8 new `jobs` columns (`is_match`, `primary_profile`, `matched_profiles`, `affinity_score`, `score_breakdown`, `match_snippets`, `matched_fields`, `user_status`) and rebuilding legacy `sources` tables to composite PK `(source_type, source_identifier)` without data loss.
  - `ScanPipeline._process_source()` evaluating `KeywordMatcher.evaluate()` prior to `JobRepository.upsert()` so persisted jobs carry complete Match Explainability on initial insert and content updates.
  - `JobRepository.upsert()` preserving `existing.user_status` (`'saved'` or `'applied'`) when a job's `content_hash` changes.
  - `reevaluate_stored_jobs()` backfilling legacy rows (`is_match IS NULL`) on startup and recalculating scores/profiles across all stored jobs when `search_profiles` change.
  - `JobRepository.query_jobs()` filtering by `match_mode`, `profile`, `user_status`, `alert_status` (`alerts_sent` join on `(job_key, content_hash)`), and excluding active `job`, `company`, and `source` rows in `dismissed_items` at query time without deleting `jobs` records.
- **Prior Art**: `tests/test_persistence.py::TestDatabaseInitialization::test_create_schema_migrates_missing_job_columns` and `test_create_schema_rebuilds_legacy_sources_primary_key`.

### Seam 2: Config Round-Trip & Hot-Reload Seam (`tests/test_config_manager.py`)
- **Interface Under Test**: `ConfigManager.load()`, `ConfigManager.update_config()`, and `CompanyRegistry` update helpers.
- **Behaviors Verified**:
  - Updating `search_profiles`, `filtering`, `sources`, or `scan_interval` via `ruamel.yaml` preserves YAML comments (`# ...`) and keeps `identifier: "*"` wildcard entries unexpanded on disk.
  - Saving `search_profiles` on a legacy `config.yaml` that only had `search_criteria` migrates the document to `search_profiles` and removes the legacy `search_criteria` key cleanly.
  - Invalid updates (e.g., term in both `required_terms` and `exclude_terms`, `hard_block_years < max_years_experience`, or all sources disabled) raise `ConfigurationError` and leave the on-disk `config.yaml` and in-memory `pipeline.app_config` untouched.
  - Valid updates write atomically and hot-reload `pipeline.app_config`, `pipeline.keyword_matcher`, and `scheduler_service` interval under `ScanPipeline._lock`.
- **Prior Art**: `tests/test_config.py` and `tests/test_registry.py`.

### Seam 3: FastAPI Bridge Seam (`tests/test_api.py` via `fastapi.testclient.TestClient`)
- **Interface Under Test**: HTTP endpoints mounted on the FastAPI app created by `create_app()`.
- **Behaviors Verified**:
  - `GET /api/jobs` filtering, sorting, pagination, `alert_status` (`alerted` vs. `pending_alert`), and query-time exclusion of dismissed items.
  - `PATCH /api/jobs/{job_key}/status` transitioning `user_status` across `new -> saved -> applied`.
  - `POST /api/dismissals` and `DELETE /api/dismissals` hiding and immediately restoring jobs in subsequent `GET /api/jobs` calls (backing the 1-click split-button, 5-second Undo toast, and Audit/Restore drawer).
  - `POST /api/config/preview` returning zero-network match counts, deltas, and scored sample matches against stored SQLite jobs without modifying database or config state.
  - `PUT /api/config` persisting changes, hot-reloading the matcher, and triggering stored-job re-evaluation.
  - `GET /api/sources` returning explicit sources and grouped Wildcard Source Rollups with aggregate counts and sanitized error drill-downs; `GET/PUT/POST /api/registries/{ats_type}` toggling and adding registry companies.
  - `POST /api/scan/trigger` defaulting to `send_notifications=False` (persisting jobs while leaving `alerts_sent` empty so `SOL-001` `needs_alert` / `is_retry` remains `True` for the next scheduled digest), returning `409 Conflict` when `ScanPipeline._lock` is held, and `GET /api/scan/status` reporting `last_run_result` funnel metrics.
- **Prior Art**: `tests/test_pipeline.py` and `tests/integration/test_registry_integration.py`.

### Seam 4: SvelteKit 5 UI & Query Builder Seam (`frontend/src/lib/` Vitest Suite)
- **Interface Under Test**: Pure TypeScript domain/state modules in `frontend/src/lib/` (specifically the `serpapi_google` Visual Query Builder serializer/parser, Search Profile conflict validator, and Preset Industry Block Pack merger).
- **Behaviors Verified**:
  - Round-trip serialization and parsing of `serpapi_google` queries (`site:boards.greenhouse.io OR site:jobs.lever.co "staff engineer" (python OR rust) -crypto`) into structured visual builder state and back, including graceful preservation of arbitrary raw query tails.
  - Client-side conflict detection between `required_terms`, `keyword_groups`, `affinity_weights`, and `exclude_terms`.
  - Idempotent merging of Preset Industry Block Packs into `FilteringRules` lists without duplicates.

---

## Out of Scope

- **Multi-User Authentication & RBAC**: The UI and API are designed for single-user, local/self-hosted operation (`127.0.0.1` or private LAN/VPN container) and do not include user login, OAuth, or multi-tenant isolation.
- **Cloud Database Migration**: Storage remains self-contained in SQLite (`WAL` mode); migrating to PostgreSQL or external cloud databases is out of scope.
- **Automated Job Application Submission**: The scanner facilitates discovery, scoring, and triage (`new | saved | applied`), and links directly to the original ATS posting for manual application; headless browser auto-apply bots are out of scope.
- **Real-Time WebSocket Log Streaming**: Polling `/api/scan/status` during active scans provides sufficient reactivity for scan progress and funnel metrics without WebSocket complexity.

## Further Notes

- **Security & Secret Hygiene**: Neither `GET /api/config` nor `GET /api/sources` may ever return raw secret values (`SMTP_PASS`, `SERPAPI_API_KEY`). Environment readiness is exposed strictly as boolean capability flags (`env_status: {serpapi_configured: bool, smtp_configured: bool}`), and all adapter error strings are sanitized via `BaseAdapter._sanitize_url()` before persistence or API serialization.
- **CLI Parity**: All existing CLI flags (`--config`, `--manual-run`, `--log-level`, `--dismiss`, `--dismiss-reason`, `--undismiss`, `--list-dismissed`) remain 100% backwards-compatible.
