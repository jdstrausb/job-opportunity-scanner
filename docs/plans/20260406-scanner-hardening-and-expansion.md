---
status: completed
date: 2026-04-06
feature: scanner-hardening-and-expansion
disciplines:
  - codebase-design
  - writing-for-agents
---

# Implementation Blueprint: Scanner Hardening & Expansion

## 1. Executive Summary

This blueprint decomposes the 4-milestone `job-opportunity-scanner` evolution into **9 vertical tracer-bullet slices** (1–2 hours each). Every slice cuts end-to-end from configuration/schema through domain models and deep module implementations to a public test seam and CLI/template output:

1. **Milestone 1 — Critical Bug Fixes (Slices 1.1–1.3)**: Restore dropped location metadata (`secondary_locations`, `is_remote`, `location_country`) across normalization and SQLite updates, elevate `NotificationMode.DIGEST` from per-source email emission to a single run-wide consolidated digest, and eliminate the hardcoded SerpAPI key in `ats_identifier_scraper/scraper.py` in favor of `EnvironmentConfig`.
2. **Milestone 2 — Proactive & Reactive Suppression (Slices 2.1–2.3)**: Add `DomainFilter` to the pre-persistence filter chain (`AgeFilter` -> `LocationFilter` -> `RoleFilter` -> `DomainFilter`) for company/domain/industry blocklists, introduce the `dismissed_items` SQLite table with `DismissedItemRepository`, and expose `--dismiss`, `--undismiss`, and `--list-dismissed` CLI flags alongside copyable `job_key` dismissal hints in email digests.
3. **Milestone 3 — Multi-Track Search Profiles & Weighted Affinity Scoring (Slices 3.1–3.3)**: Introduce multi-track `search_profiles` with term-level `affinity_weights`, `title_boost_multiplier`, and `min_affinity_score` behind the `KeywordMatcher.evaluate()` interface while preserving 100% backwards compatibility with legacy `search_criteria`.
4. **Milestone 4 — Discovery Adapters (Slices 4.1–4.2)**: Extend the `BaseAdapter` (`fetch_jobs(source_config) -> list[RawJob]`) seam with `RssAdapter` (RSS 2.0 & Atom 1.0 standard-library XML parsing) and `SerpApiSearchAdapter` (Google search discovery with direct JSON hydration against Greenhouse, Lever, and Ashby endpoints).

---

## 2. Architectural Decisions & Guardrails

### 2.1 Deep Modules & Seam Placement (`codebase-design`)
- **Single External Seam per Subsystem**:
  - **Normalization Seam**: [`JobNormalizer.normalize(raw_job, source_config) -> NormalizationResult`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/normalization/service.py#L57-L164) hides hashing, existing-job lookup, text sanitization, and location metadata propagation.
  - **Pre-Persistence Filter Seam**: [`FilteringService.evaluate_job(job, filter_context) -> FilterResult`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/filtering/service.py#L31-L139) encapsulates the 4-stage short-circuiting chain (`AgeFilter`, `LocationFilter`, `RoleFilter`, `DomainFilter`) behind a single call.
  - **Matching & Affinity Seam**: [`KeywordMatcher.evaluate(job, matchable_text) -> MatchResult`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/matching/engine.py#L55-L224) absorbs multi-track profile iteration, boolean gates, word-boundary/contextual verification, and weighted affinity scoring without altering the caller's call signature in `ScanPipeline`.
  - **Adapter Discovery Seam**: [`BaseAdapter.fetch_jobs(source_config) -> list[RawJob]`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/adapters/base.py#L73-L95) and [`get_adapter(source_config, advanced_config, env_config=None) -> BaseAdapter`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/adapters/factory.py#L16-L73) conceal protocol differences across direct ATS boards, RSS/Atom feeds, and SerpAPI-discovered ATS URLs with JSON hydration.

### 2.2 Database Migration Safety (SQLite WAL & `create_schema`)
- **Constraint**: [`create_schema()`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/persistence/schema.py#L277-L300) invokes `Base.metadata.create_all(engine, checkfirst=True)`. In SQLite, `create_all()` creates missing tables (such as `dismissed_items`) idempotently, **but does not alter existing tables** if columns were added after initial database creation.
- **Resolution**:
  1. `DismissedItemModel` is introduced as a standalone table (`dismissed_items`) with composite primary key `(item_key, scope)` and index `idx_dismissed_scope_key`. Calling `Base.metadata.create_all(engine, checkfirst=True)` on an existing `data/job_scanner.db` adds `dismissed_items` without touching `jobs`, `sources`, or `alerts_sent`.
  2. To protect pre-existing `job_scanner.db` files created prior to the location-metadata columns on `jobs`, `create_schema(engine)` will execute an idempotent `_ensure_table_columns(engine)` helper using `sqlalchemy.inspect(engine).get_columns("jobs")` inside `with engine.begin() as conn:` and emit `ALTER TABLE jobs ADD COLUMN ...` for any missing nullable column (`secondary_locations JSON`, `is_remote BOOLEAN`, `location_country VARCHAR(255)`) under WAL mode.

### 2.3 Backwards Compatibility (`search_criteria` vs. `search_profiles`)
- **Constraint**: Existing deployments and test suites define `search_criteria` in `config.yaml` and instantiate `AppConfig(sources=..., search_criteria=...)`.
- **Resolution**:
  - `AppConfig` accepts `search_criteria: Optional[SearchCriteria] = None` and `search_profiles: List[SearchProfile] = Field(default_factory=list)`.
  - `AppConfig.validate_sources_and_compute_fields()` enforces that at least one of `search_criteria` or `search_profiles` (with $\ge 1$ enabled profile) is configured.
  - `AppConfig.get_effective_profiles() -> List[SearchProfile]` returns enabled `search_profiles` when present, or synthesizes `[SearchProfile(name="default", **self.search_criteria.model_dump())]` when only `search_criteria` is supplied. Conversely, if only `search_profiles` is supplied in YAML, `self.search_criteria` is populated from the first enabled profile so legacy callers reading `app_config.search_criteria` continue to work unchanged.

### 2.4 Secret Hygiene (`SERPAPI_API_KEY`)
- **Constraint**: [`ats_identifier_scraper/scraper.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/ats_identifier_scraper/scraper.py#L15) currently contains a hardcoded `api_key` string literal.
- **Resolution**:
  - Remove the literal from `ats_identifier_scraper/scraper.py`.
  - Add `serpapi_api_key: Optional[str] = None` to [`EnvironmentConfig`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/config/environment.py#L12-L35), populated via `os.getenv("SERPAPI_API_KEY")` (trimmed; empty string normalized to `None`).
  - Both `ats_identifier_scraper/scraper.py` and `SerpApiSearchAdapter` read the key strictly from `EnvironmentConfig` / environment variables, defaulting to `None` when unset and logging a structured, actionable error (`event="adapter.serpapi.missing_api_key"`) without leaking credentials or crashing the daemon.

### 2.5 Global Run-Level Digest Accumulation
- **Constraint**: [`ScanPipeline._process_source()`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/pipeline/runner.py#L369-L412) currently calls `self.notification_service.send_digest()` inside the per-source loop, causing wildcard runs to send one digest email per matching company.
- **Resolution**:
  - Keep per-source `get_session()` boundaries for fetching, normalizing, filtering, and persisting jobs (`JobRepository`, `SourceRepository`).
  - In `NotificationMode.DIGEST`, `_process_source()` collects `matches_to_notify: List[CandidateMatch]` and returns them alongside `SourceRunStats` to `ScanPipeline.run_once()`.
  - After the source loop finishes in `run_once()`, if `all_digest_candidates` is non-empty, `run_once()` opens a dedicated `with get_session() as session:` block, instantiates `AlertRepository(session)`, calls `self.notification_service.send_digest(all_digest_candidates, self.env_config, self.app_config.email, alert_repo, run_id)` **once**, and updates per-source `notified_count` / `alerts_sent` metrics before constructing `PipelineRunResult`.
  - In `NotificationMode.LEGACY`, per-source `send_notifications()` remains in `_process_source()`.

---

## 3. Sequence-Split Vertical Slice Breakdown

### Milestone 1: Critical Bug Fixes

#### Slice 1.1: Normalization Location Metadata Pass-Through & Persistence Update
- **Goal**: Pass `secondary_locations`, `is_remote`, and `location_country` from `RawJob` into `Job` during normalization, persist updates to those columns in `JobRepository.upsert()`, and ensure idempotent SQLite column migration in `create_schema()`.
- **Files Modified**:
  - [`app/normalization/service.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/normalization/service.py#L124-L139) — Pass `secondary_locations=raw_job.secondary_locations`, `is_remote=raw_job.is_remote`, and `location_country=raw_job.location_country` into `Job(...)` inside `JobNormalizer.normalize()`.
  - [`app/persistence/repositories.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/persistence/repositories.py#L111-L135) — Update `existing.secondary_locations`, `existing.is_remote`, and `existing.location_country` in `JobRepository.upsert()`.
  - [`app/persistence/schema.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/persistence/schema.py#L277-L300) — Add `_ensure_table_columns(engine)` in `create_schema()` to inspect `jobs` and run `ALTER TABLE jobs ADD COLUMN ...` if any of the 3 location metadata columns are absent on legacy SQLite files.
  - [`tests/test_normalization.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_normalization.py) — Add unit test verifying all 3 fields survive `JobNormalizer.normalize()`.
  - [`tests/test_persistence.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_persistence.py) — Add repository test verifying `JobRepository.upsert()` updates all 3 fields on an existing `job_key` and `_ensure_table_columns()` upgrades a legacy `jobs` table.
- **Public Testing Seam**:
  - `tests/test_normalization.py::TestJobNormalizer::test_normalize_preserves_location_metadata`
  - `tests/test_persistence.py::TestJobRepository::test_upsert_updates_location_metadata`
  - `tests/test_persistence.py::TestDatabaseInitialization::test_create_schema_migrates_missing_job_columns`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_normalization.py tests/test_persistence.py -k "location_metadata or migrates_missing_job_columns" --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - `raw_job.secondary_locations` being empty (`[]`) vs. populated (`["Remote - US", "New York, NY"]`) — `JobModel` stores `None` when empty and deserializes `None` back to `[]` in `to_domain()`.
  - Explicit `is_remote=False` vs. `is_remote=None` — must preserve boolean `False` rather than coercing falsy values to `None`.
  - Pre-existing `job_scanner.db` files missing the 3 columns failing with `OperationalError: no such column`.

---

#### Slice 1.2: Global Run-Level Digest Accumulation Across Sources
- **Goal**: Aggregate `CandidateMatch` items across all enabled sources during `ScanPipeline.run_once()` and dispatch a single consolidated digest email per scan run when `notification_mode == NotificationMode.DIGEST`.
- **Files Modified**:
  - [`app/pipeline/runner.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/pipeline/runner.py#L125-L465) — Refactor `_process_source()` to return `tuple[SourceRunStats, list[CandidateMatch]]` (or append to a run-level list when in `NotificationMode.DIGEST`), move `self.notification_service.send_digest(...)` to a post-loop `_send_global_digest(all_matches, source_stats_map, run_id)` helper in `run_once()` inside its own `get_session()` transaction, and attribute `notified_count` / `alerts_sent` (or error flags) back to the participating `SourceRunStats`.
  - [`app/pipeline/models.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/pipeline/models.py#L81-L98) — Add `recompute_aggregates()` on `PipelineRunResult` so post-loop digest notification counts and error states are reflected in `total_notified`, `alerts_sent`, `total_errors`, and `had_errors`.
  - [`tests/test_pipeline.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_pipeline.py) — Update `sample_raw_jobs` / `app_config` fixtures so test jobs pass default `FilteringRules` (mid-level engineering titles with `Remote` locations and valid UTC timestamps), verify `NotificationMode.DIGEST` calls `send_digest` exactly once across multiple sources, and verify `NotificationMode.LEGACY` calls `send_notifications` per source.
- **Public Testing Seam**:
  - `tests/test_pipeline.py::TestScanPipeline::test_run_once_sends_single_global_digest_across_sources`
  - `tests/test_pipeline.py::TestScanPipeline::test_run_once_digest_empty_when_no_matches`
  - `tests/test_pipeline.py::TestScanPipeline::test_run_once_legacy_mode_sends_per_job_notifications`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_pipeline.py --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - Wildcard expansion scanning 50+ sources and triggering 50 separate SMTP digest connections instead of 1.
  - Digest SMTP failure after all sources succeeded — must mark `result.had_errors = True` and increment `total_errors` without rolling back already-persisted jobs in `jobs` or health timestamps in `sources`.
  - Zero matches across all sources — must not invoke `send_digest()` or open an unnecessary notification transaction.

---

#### Slice 1.3: SerpAPI Environment Secret Hygiene
- **Goal**: Purge the hardcoded SerpAPI key from `ats_identifier_scraper/scraper.py`, centralize `SERPAPI_API_KEY` in `EnvironmentConfig` defaulting strictly to `None`, and fail gracefully with actionable logging when unset.
- **Files Modified / Created**:
  - [`app/config/environment.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/config/environment.py#L12-L148) — Add `serpapi_api_key: Optional[str] = None` attribute to `EnvironmentConfig.__init__()` and load `os.getenv("SERPAPI_API_KEY")` in `load_environment_config()` (stripping whitespace and normalizing empty strings to `None`).
  - [`ats_identifier_scraper/scraper.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/ats_identifier_scraper/scraper.py#L1-L23) — Remove hardcoded `"api_key"` on line 15; load `SERPAPI_API_KEY` from environment (`os.getenv("SERPAPI_API_KEY")`), log an actionable error message if `None`, and return `None` without making an unauthenticated HTTP request.
  - [`.env.example`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/.env.example) — Document optional `SERPAPI_API_KEY=` variable.
  - [`tests/test_config.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_config.py) — Add unit tests for `EnvironmentConfig.serpapi_api_key`.
  - `tests/test_scraper_hygiene.py` (new) — Add unit tests verifying `ats_identifier_scraper/scraper.py` contains no hardcoded hex key and fails gracefully when `SERPAPI_API_KEY` is unset.
- **Public Testing Seam**:
  - `tests/test_config.py::TestEnvironmentConfig::test_serpapi_api_key_defaults_to_none_when_unset`
  - `tests/test_config.py::TestEnvironmentConfig::test_serpapi_api_key_loaded_from_env`
  - `tests/test_scraper_hygiene.py::test_get_career_page_url_without_api_key_fails_gracefully`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_config.py tests/test_scraper_hygiene.py -k "serpapi or without_api_key" --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - `SERPAPI_API_KEY="   "` (whitespace-only in `.env`) — must normalize to `None` rather than passing a blank key to SerpAPI.
  - Missing `SERPAPI_API_KEY` in standard ATS-only deployments — must never raise `ConfigurationError` at startup since SerpAPI is optional unless a `serpapi_google` source or scraper utility is invoked.

---

### Milestone 2: Proactive & Reactive Suppression

#### Slice 2.1: Proactive Pre-Persistence `DomainFilter` (Company, Domain, & Industry Blocklists)
- **Goal**: Add a 4th stage (`DomainFilter`) to `FilteringService` to reject postings from blocked companies, blocked URL domains, or blocked industry/domain keywords (with allow-term overrides) prior to database persistence.
- **Files Modified / Created**:
  - [`app/config/models.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/config/models.py#L217-L418) — Extend `FilteringRules` with:
    - `enable_domain_filter: bool = Field(True, description="Enable company and industry domain filtering")`
    - `blocked_companies: List[str] = Field(default_factory=list, description="Company names to block (case-insensitive)")`
    - `blocked_domains: List[str] = Field(default_factory=list, description="Industry/domain terms or URL domains to block (word-boundary matched)")`
    - `domain_allow_terms: List[str] = Field(default_factory=list, description="Terms that override blocked_domains")`
    - Include `blocked_companies`, `blocked_domains`, and `domain_allow_terms` in `FilteringRules.normalize_lists()`.
  - `app/filtering/domain_filter.py` (new) — Implement `DomainFilter.evaluate(job: Job, filter_context: FilterContext) -> FilterResult`:
    1. If `not rules.enable_domain_filter`, return `FilterResult(should_process=True, filter_metadata={"domain_filter": "disabled"})`.
    2. Check `job.company.strip().lower()` against `rules.blocked_companies`; if matched, return `FilterResult(should_process=False, filter_reason="domain_filter_company_blocked", domain_blocked=True, filter_metadata={"domain_filter": "company_blocked", "blocked_company": matched_company})`.
    3. If no `domain_allow_terms` match `title` or `description`, check `rules.blocked_domains` against `job.url` hostname and word-boundary regex (`\b...\b`) in `job.title` and `job.description`; if matched, return `FilterResult(should_process=False, filter_reason="domain_filter_domain_blocked", domain_blocked=True, filter_metadata={"domain_filter": "domain_blocked", "blocked_domain": matched_term})`.
  - [`app/filtering/models.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/filtering/models.py#L29-L61) — Add `domain_blocked: Optional[bool] = None` to `FilterResult`.
  - [`app/filtering/service.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/filtering/service.py#L19-L139) — Instantiate `self.domain_filter = DomainFilter()` and run `self.domain_filter.evaluate(job, filter_context)` after `self.role_filter`, merging metadata on short-circuit and final pass.
  - [`app/filtering/__init__.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/filtering/__init__.py) — Export `DomainFilter`.
  - [`config.example.yaml`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/config.example.yaml) — Document `enable_domain_filter`, `blocked_companies`, `blocked_domains`, and `domain_allow_terms`.
  - `tests/test_domain_filter.py` (new) — Comprehensive unit tests for `DomainFilter` and `FilteringService` integration.
- **Public Testing Seam**:
  - `tests/test_domain_filter.py::TestDomainFilter::test_blocks_configured_company_and_domain_terms`
  - `tests/test_domain_filter.py::TestDomainFilter::test_domain_allow_terms_override_blocked_domain`
  - `tests/test_domain_filter.py::TestFilteringServiceWithDomainFilter::test_filtering_service_short_circuits_on_domain_filter`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_domain_filter.py --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - Substring false positives on short domain terms (e.g., blocking `"bet"` for gambling must not block `"alphabet"` or `"better"` — enforced via `\b` word-boundary regex).
  - Case sensitivity and whitespace variations in company names (`"  Example Corp "` vs. `"example corp"`).

---

#### Slice 2.2: Reactive Suppression Schema (`dismissed_items`), Repository, & Pipeline Integration
- **Goal**: Persist user-dismissed jobs, companies, and sources in a `dismissed_items` SQLite table and suppress matching items in `ScanPipeline._process_source()` before job persistence and matching.
- **Files Modified**:
  - [`app/domain/models.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/domain/models.py) — Add `DismissalScope(str, Enum)` (`JOB = "job"`, `COMPANY = "company"`, `SOURCE = "source"`) and `DismissedItem(BaseModel)` (`item_key: str`, `scope: DismissalScope = DismissalScope.JOB`, `reason: Optional[str] = None`, `dismissed_at: datetime`). Normalize `item_key` to stripped lowercase.
  - [`app/persistence/schema.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/persistence/schema.py) — Add `DismissedItemModel(Base)` (`__tablename__ = "dismissed_items"`, composite PK `item_key = Column(String(255), primary_key=True)` + `scope = Column(String(50), primary_key=True)`, `reason = Column(Text, nullable=True)`, `dismissed_at = Column(String(50), nullable=False)`, `Index("idx_dismissed_scope_key", "scope", "item_key")`) with `.to_domain()` and `.from_domain()`.
  - [`app/persistence/repositories.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/persistence/repositories.py) — Implement `DismissedItemRepository(session)` with:
    - `dismiss(item_key: str, scope: str = "job", reason: Optional[str] = None, dismissed_at: Optional[datetime] = None) -> DismissedItem` (idempotent upsert).
    - `undismiss(item_key: str, scope: str = "job") -> bool` (returns `True` if deleted, `False` if absent).
    - `is_dismissed(job: Job) -> tuple[bool, Optional[DismissedItem]]` (checks `(job.job_key, "job")`, `(job.company.strip().lower(), "company")`, and `(job.source_identifier.strip().lower(), "source")` in a single SQL `OR` query).
    - `list_all(scope: Optional[str] = None) -> List[DismissedItem]`.
  - [`app/persistence/__init__.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/persistence/__init__.py) — Export `DismissedItemRepository`.
  - [`app/pipeline/runner.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/pipeline/runner.py#L200-L310) — Instantiate `dismissed_repo = DismissedItemRepository(session)` in `_process_source()`. Immediately after `normalizer.normalize(raw_job, source_config)`, evaluate `is_dismissed, dismissed_item = dismissed_repo.is_dismissed(norm_result.job)`. When `True`, increment `stats.filtered_count`, record `stats.filter_reasons[f"dismissed_{dismissed_item.scope}"]`, log at `DEBUG`, and `continue`.
  - [`tests/test_persistence.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_persistence.py) & [`tests/test_pipeline.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_pipeline.py) — Add repository and pipeline suppression tests.
- **Public Testing Seam**:
  - `tests/test_persistence.py::TestDismissedItemRepository::test_dismiss_and_is_dismissed_across_scopes`
  - `tests/test_persistence.py::TestDismissedItemRepository::test_dismiss_idempotent_and_undismiss`
  - `tests/test_pipeline.py::TestScanPipeline::test_run_once_skips_dismissed_jobs_and_companies`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_persistence.py tests/test_pipeline.py -k "dismiss" --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - Duplicate dismissal of the same `job_key` or `company` — `dismiss()` performs an idempotent update of `reason` and `dismissed_at` rather than raising `IntegrityError`.
  - Existing `job_scanner.db` upgraded in place — `Base.metadata.create_all(engine, checkfirst=True)` creates `dismissed_items` without altering or locking existing tables.

---

#### Slice 2.3: CLI Dismissal Management Commands & Digest `job_key` Surfacing
- **Goal**: Expose `--dismiss`, `--dismiss-reason`, `--undismiss`, and `--list-dismissed` CLI flags in `app/main.py` that operate without requiring a full config/scan cycle when managing dismissals, and surface `job_key` in digest templates so users can copy-paste a key to dismiss.
- **Files Modified**:
  - [`app/main.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/main.py#L81-L140) — Add CLI flags:
    - `--dismiss <target>`: Accepts `<job_key>`, `job:<job_key>`, `company:<name>`, or `source:<identifier>`.
    - `--dismiss-reason <text>`: Optional reason paired with `--dismiss`.
    - `--undismiss <target>`: Removes the specified dismissal entry.
    - `--list-dismissed`: Lists all active records in `dismissed_items` and exits `0`.
    - Execute dismissal commands right after `init_database(database_url)` (resolving `DATABASE_URL` from `.env` or default `sqlite:///./data/job_scanner.db` without requiring SMTP environment variables to be set for quick CLI dismissal ops) and exit `0`.
  - [`app/notifications/email_templates/digest_body.html.j2`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/notifications/email_templates/digest_body.html.j2#L198-L228) & [`app/notifications/email_templates/digest_body.txt.j2`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/notifications/email_templates/digest_body.txt.j2#L15-L24) — Display `job.job_key[:12]` (and full `job.job_key` in a muted monospace footer / text line `Dismiss: job-scanner --dismiss {{ job.job_key }}`).
  - [`tests/test_main.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_main.py) & [`tests/test_notifications_templates.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_notifications_templates.py) — Add CLI and template tests.
- **Public Testing Seam**:
  - `tests/test_main.py::TestDismissCLI::test_cli_dismiss_and_list_and_undismiss_flow`
  - `tests/test_notifications_templates.py::TestDigestTemplates::test_digest_renders_job_key_for_dismissal`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_main.py tests/test_notifications_templates.py -k "dismiss" --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - Running `job-scanner --dismiss <job_key>` in a shell without `SMTP_HOST` exported — CLI dismissal management resolves `DATABASE_URL` directly (`os.getenv("DATABASE_URL", "sqlite:///./data/job_scanner.db")`) before full `load_runtime_config()` so local CLI triage works with zero SMTP setup.
  - Malformed scope prefix (e.g., `--dismiss invalid_scope:foo`) — prints clear error to `stderr` with valid scopes (`job`, `company`, `source`) and exits `1`.

---

### Milestone 3: Multi-Track Search Profiles & Weighted Affinity Scoring

#### Slice 3.1: Multi-Track `SearchProfile` Config Schema & Backwards-Compatible Normalization
- **Goal**: Introduce `SearchProfile` (extending `SearchCriteria` with `name`, `enabled`, `affinity_weights`, `title_boost_multiplier`, and `min_affinity_score`) and `AppConfig.search_profiles` while keeping legacy `search_criteria`-only configurations 100% functional.
- **Files Modified**:
  - [`app/config/models.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/config/models.py#L65-L159) — Define `SearchProfile(SearchCriteria)`:
    - `name: str = Field(..., min_length=1, description="Unique profile track name")`
    - `enabled: bool = Field(True, description="Whether this search profile is active")`
    - `affinity_weights: Dict[str, float] = Field(default_factory=dict, description="Term-to-weight mapping for affinity scoring")`
    - `title_boost_multiplier: float = Field(2.0, ge=1.0, le=10.0, description="Multiplier when matched terms appear in job title")`
    - `min_affinity_score: float = Field(0.0, ge=0.0, description="Minimum affinity score required to trigger a match")`
    - Normalize `affinity_weights` keys to stripped lowercase and validate against `exclude_terms` conflicts.
  - [`app/config/models.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/config/models.py#L420-L492) — Update `AppConfig`:
    - Change `search_criteria: Optional[SearchCriteria] = Field(None, description="Legacy single-track keyword matching rules")`.
    - Add `search_profiles: List[SearchProfile] = Field(default_factory=list, description="Multi-track search profiles with affinity scoring")`.
    - Validate in `validate_sources_and_compute_fields()` that at least one of `search_criteria` or an enabled profile in `search_profiles` is present, and profile names are unique.
    - Populate `self.search_criteria` from the first enabled profile when omitted, and add `get_effective_profiles() -> List[SearchProfile]`.
  - [`verify_config.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/verify_config.py#L25-L65) — Accept either `search_criteria` or `search_profiles` as valid top-level search configuration.
  - [`config.example.yaml`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/config.example.yaml) — Document `search_profiles` alongside `search_criteria`.
  - [`tests/test_config.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_config.py) — Add unit tests for legacy fallback, multi-profile parsing, duplicate profile name rejection, and affinity weight normalization.
- **Public Testing Seam**:
  - `tests/test_config.py::TestSearchProfilesConfig::test_legacy_search_criteria_synthesizes_default_profile`
  - `tests/test_config.py::TestSearchProfilesConfig::test_multi_track_search_profiles_validation_and_conflict_checks`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_config.py --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - Omitting `search_profiles` in existing `config.yaml` files — seamlessly synthesizes the `"default"` `SearchProfile` from `search_criteria`.
  - Configuring `search_profiles` where all profiles have `enabled: false` and no `search_criteria` — fails fast at config load time with `ConfigurationError`.
  - Term present in both `affinity_weights` and `exclude_terms` within the same profile — rejected by `SearchProfile` validator.

---

#### Slice 3.2: Multi-Track `KeywordMatcher` Evaluation & Weighted Affinity Scoring
- **Goal**: Deepen `KeywordMatcher.evaluate()` to evaluate jobs across one or more `SearchProfile` tracks, compute weighted affinity scores with title boosts, enforce `min_affinity_score`, and populate profile/affinity metadata on `MatchResult`.
- **Files Modified**:
  - [`app/matching/models.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/matching/models.py#L15-L83) — Extend `MatchResult` with:
    - `matched_profiles: List[str] = field(default_factory=list)`
    - `primary_profile: Optional[str] = None`
    - `affinity_score: float = 0.0`
    - `score_breakdown: Dict[str, float] = field(default_factory=dict)`
  - [`app/matching/engine.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/matching/engine.py#L24-L225) — Update `KeywordMatcher`:
    - `__init__(self, search_criteria: Union[SearchCriteria, Sequence[SearchProfile]], logger_instance=None)`: Accept either a single `SearchCriteria`/`SearchProfile` or a list of `SearchProfile` objects, normalizing internally to `self.profiles: List[SearchProfile]` while keeping `self.search_criteria` pointed at the primary/first profile for backwards-compatible attribute access.
    - `_evaluate_profile(self, profile: SearchProfile, field_index: Dict[str, str], matchable_text: MatchableText)`: Run boolean checks (`required_terms`, `keyword_groups`, `exclude_terms`, `boundary_terms`, `contextual_terms`). If boolean gates pass, calculate `affinity_score`:
      - Each matched required or group term contributes base weight `1.0` (or `profile.affinity_weights[term]` if explicitly weighted), multiplied by `profile.title_boost_multiplier` when present in `field_index["title"]`.
      - Additional non-required terms in `profile.affinity_weights` that match in `field_index` (respecting `boundary_terms` and `contextual_terms`) add `weight * (profile.title_boost_multiplier if in_title else 1.0)` to `affinity_score` and `score_breakdown`.
      - If `affinity_score < profile.min_affinity_score`, the profile does not match.
    - Aggregate across all profiles in `evaluate(job, matchable_text)`: If $\ge 1$ profile matches, select the highest-scoring profile as `primary_profile`, list all passing profile names in `matched_profiles` (ordered by score descending), and include profile & affinity score in `MatchResult.summary`.
  - [`tests/test_matching.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_matching.py) — Add multi-track and affinity scoring test suite while verifying all existing `test_matching.py` tests continue to pass untouched.
- **Public Testing Seam**:
  - `tests/test_matching.py::TestMultiTrackAffinityMatcher::test_evaluates_multiple_profiles_and_selects_highest_affinity_track`
  - `tests/test_matching.py::TestMultiTrackAffinityMatcher::test_title_boost_multiplier_and_affinity_weights_calculation`
  - `tests/test_matching.py::TestMultiTrackAffinityMatcher::test_min_affinity_score_threshold_suppresses_low_score_match`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_matching.py --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - Job fails Track A (e.g., missing `"rust"`) but passes Track B (e.g., `"python"` + `"fastapi"`) — `is_match` must be `True` with `primary_profile="backend_python"` and clean `missing_required_terms=set()` from the winning track.
  - Double-counting terms present in both `required_terms` and `affinity_weights` — each unique matched term is scored once per profile using its configured `affinity_weights.get(term, 1.0)`.

---

#### Slice 3.3: Pipeline Wiring & Digest Ranking by Affinity Score
- **Goal**: Wire `app_config.get_effective_profiles()` into `KeywordMatcher` in `app/main.py` and rank jobs in `DigestBuilder` and digest templates by `affinity_score` descending (with `posted_at` tie-breaking).
- **Files Modified**:
  - [`app/main.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/main.py#L149-L155) — Instantiate `KeywordMatcher(app_config.get_effective_profiles())`.
  - [`app/notifications/digest.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/notifications/digest.py#L51-L60) — Update `CompanyGroup.sort_jobs_by_date()` to sort by `(getattr(c.match_result, "affinity_score", 0.0), c.job.posted_at or datetime.min)` descending, and enrich `DigestBuilder._build_job_summary()` with `"primary_profile"`, `"matched_profiles"`, and `"affinity_score"`.
  - [`app/notifications/email_templates/digest_body.html.j2`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/notifications/email_templates/digest_body.html.j2#L204-L220) & [`app/notifications/email_templates/digest_body.txt.j2`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/notifications/email_templates/digest_body.txt.j2#L15-L24) — Render profile track badge and affinity score when present.
  - [`tests/test_digest.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_digest.py) & [`tests/test_main.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_main.py) — Add unit tests verifying digest sorting by affinity score and profile metadata rendering.
- **Public Testing Seam**:
  - `tests/test_digest.py::TestDigestBuilder::test_digest_sorts_by_affinity_score_and_includes_profile_metadata`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_digest.py tests/test_main.py --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - Legacy `MatchResult` fixtures in tests that do not specify `affinity_score` or `primary_profile` — default values (`0.0`, `None`) preserve date-descending sort order and template rendering without `StrictUndefined` Jinja2 errors.

---

### Milestone 4: Discovery Adapters (`RssAdapter` & `SerpApiSearchAdapter`)

#### Slice 4.1: `RssAdapter` (`type: "rss"`) & Discovery Source Type Registration
- **Goal**: Register `"rss"` and `"serpapi_google"` across configuration and domain validators, and implement `RssAdapter(BaseAdapter)` supporting RSS 2.0 and Atom 1.0 XML job feeds.
- **Files Modified / Created**:
  - [`app/config/models.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/config/models.py#L11-L63) — Add `RSS = "rss"` and `SERPAPI_GOOGLE = "serpapi_google"` to `ATSType`. Add optional `options: Dict[str, Any] = Field(default_factory=dict, description="Adapter-specific configuration options")` to `SourceConfig`.
  - [`app/domain/models.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/domain/models.py#L120-L128) — Update `valid_types` in `Job.validate_source_type()` (line 124) and `SourceStatus.validate_source_type()` (line 215) to `{"greenhouse", "lever", "ashby", "rss", "serpapi_google"}`.
  - [`app/adapters/base.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/adapters/base.py) — Add `_make_text_request(url, method="GET", headers=None, params=None) -> str` to `BaseAdapter` reusing session/timeout/error handling for non-JSON XML payloads.
  - `app/adapters/rss.py` (new) — Implement `RssAdapter(BaseAdapter)` (`ADAPTER_NAME = "rss"`):
    - Fetch feed XML from `source_config.identifier` via `self._make_text_request(source_config.identifier)`.
    - Parse XML with `xml.etree.ElementTree.fromstring` (raising `AdapterResponseError` on `ET.ParseError`).
    - Support both RSS 2.0 (`./channel/item` or `.//item`) and Atom (`{http://www.w3.org/2005/Atom}entry`).
    - Map items to `RawJob`: derive `external_id` from `<guid>`/`<id>` or `<link>`, `title` from `<title>`, `company` from `<dc:creator>` / `<author><name>` / `"Company: Title"` prefix or `source_config.name`, `location` from `<region>` / `<location>` / `source_config.options.get("default_location")`, `description` via `self._clean_html(...)`, `url` from `<link>`, and `posted_at` via RFC-2822 (`email.utils.parsedate_to_datetime`) or ISO-8601 (`self._parse_timestamp`).
  - [`app/adapters/factory.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/adapters/factory.py) & [`app/adapters/__init__.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/adapters/__init__.py) — Register `"rss": RssAdapter` and export `RssAdapter`.
  - [`verify_config.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/verify_config.py#L49) — Add `'rss'` and `'serpapi_google'` to `valid_types`.
  - [`tests/test_adapters.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_adapters.py) — Add RSS 2.0, Atom 1.0, malformed XML, and HTTP error tests for `RssAdapter`.
- **Public Testing Seam**:
  - `tests/test_adapters.py::TestRssAdapter::test_fetch_jobs_parses_rss2_and_atom_feeds`
  - `tests/test_adapters.py::TestRssAdapter::test_fetch_jobs_malformed_xml_raises_response_error`
  - `tests/test_adapters.py::TestAdapterFactory::test_get_adapter_returns_rss_adapter`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_adapters.py -k "TestRssAdapter or test_get_adapter_returns_rss_adapter" --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - RSS items missing `<guid>` — falls back to `<link>` so `external_id` is always deterministic.
  - RFC-2822 `<pubDate>` timestamps with named timezones or offsets — normalized to UTC-aware `datetime` via `email.utils.parsedate_to_datetime`.
  - Normalization failing on `Job(source_type="rss", ...)` due to hardcoded `valid_types` in `app/domain/models.py` — resolved by updating both `Job` and `SourceStatus` validators in the same slice.

---

#### Slice 4.2: `SerpApiSearchAdapter` (`type: "serpapi_google"`) with Direct ATS JSON Hydration
- **Goal**: Implement `SerpApiSearchAdapter(BaseAdapter)` to execute Google search queries via SerpAPI, detect direct ATS posting URLs (Greenhouse, Lever, Ashby), and hydrate full `RawJob` payloads from their public JSON APIs.
- **Files Modified / Created**:
  - `app/adapters/serpapi_google.py` (new) — Implement `SerpApiSearchAdapter(BaseAdapter)` (`ADAPTER_NAME = "serpapi_google"`):
    - Accept `api_key: Optional[str] = None` in `__init__(..., api_key: Optional[str] = None)`, falling back to `os.getenv("SERPAPI_API_KEY")`.
    - In `fetch_jobs(source_config)`: if `not self.api_key`, log `event="adapter.serpapi.missing_api_key"` at `ERROR` and raise `AdapterConfigurationError("SERPAPI_API_KEY is required for serpapi_google sources")`.
    - Query `https://serpapi.com/search.json` via `self._make_request()` with `params={"engine": "google", "q": source_config.identifier, "api_key": self.api_key, "num": source_config.options.get("num_results", 20)}`.
    - Parse `organic_results` and match each `link` against ATS URL patterns:
      1. **Greenhouse** (`(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)/jobs/(\d+)`): Fetch `https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}` and transform via Greenhouse JSON field mapping (`id`, `title`, `location.name`, `content`, `absolute_url`, `first_published`, `updated_at`).
      2. **Lever** (`jobs\.lever\.co/([a-zA-Z0-9_-]+)/([0-9a-fA-F-]{36})`): Fetch `https://api.lever.co/v0/postings/{company}/{job_id}?mode=json` and transform via Lever JSON field mapping (`id`, `text`, `categories.location`, `descriptionPlain`, `hostedUrl`, `createdAt`).
      3. **Ashby** (`jobs\.ashbyhq\.com/([a-zA-Z0-9_.-]+)/([0-9a-fA-F-]{36})`): Fetch `https://api.ashbyhq.com/posting-api/job-board/{org}` (memoized per `org` within `fetch_jobs` to avoid redundant board fetches), locate the matching `job["id"] == job_id`, and transform via Ashby JSON field mapping (including `secondaryLocations`, `isRemote`, `addressCountry`).
      4. **Fallback**: If direct hydration returns 404/5xx or the URL is a non-hydrated job posting with a non-empty `snippet` and `title`, construct a fallback `RawJob` from the SerpAPI organic result (`external_id=compute_url_hash(link)`, `title`, `company`, `description=snippet`, `url=link`).
  - [`app/adapters/factory.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/adapters/factory.py#L16-L73) — Register `"serpapi_google": SerpApiSearchAdapter` and accept optional `env_config: Optional[EnvironmentConfig] = None` in `get_adapter()` to pass `api_key=env_config.serpapi_api_key if env_config else None`.
  - [`app/pipeline/runner.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/pipeline/runner.py#L211) — Pass `env_config=self.env_config` to `get_adapter(source_config, self.app_config.advanced, self.env_config)` (preserving backwards-compatible 2-arg call signature for existing tests/callers).
  - [`app/adapters/__init__.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/app/adapters/__init__.py) & [`config.example.yaml`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/config.example.yaml) — Export `SerpApiSearchAdapter` and document `serpapi_google` and `rss` example sources.
  - [`tests/test_adapters.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_adapters.py) — Add unit tests mocking SerpAPI search responses and direct ATS JSON hydration responses.
- **Public Testing Seam**:
  - `tests/test_adapters.py::TestSerpApiSearchAdapter::test_fetch_jobs_hydrates_greenhouse_lever_and_ashby_urls`
  - `tests/test_adapters.py::TestSerpApiSearchAdapter::test_fetch_jobs_falls_back_to_snippet_on_hydration_404`
  - `tests/test_adapters.py::TestSerpApiSearchAdapter::test_fetch_jobs_missing_api_key_raises_configuration_error`
- **Verification Command**:
  ```bash
  uv run pytest tests/test_adapters.py -k "TestSerpApiSearchAdapter" --no-cov -v
  ```
- **Failure Modes / Edge Cases Guarded Against**:
  - Multiple SerpAPI organic results pointing to the same Ashby organization — board JSON is cached in a per-call dict so `api.ashbyhq.com` is queried at most once per organization slug.
  - Stale Google index link returning HTTP 404 on the ATS endpoint — gracefully falls back or skips without aborting hydration of remaining search hits.
  - Redacting `api_key` from any logged URL or exception message in `SerpApiSearchAdapter`.

---

## 4. Master Verification Gate

Upon completing all 9 slices, the full verification suite must pass cleanly:

```bash
uv run pytest -v
uv run ruff check .
uv run mypy app
uv run python verify_config.py
```
