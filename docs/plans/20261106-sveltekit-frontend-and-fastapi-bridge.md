---
title: "Implementation Blueprint: SvelteKit 5 Frontend & Co-Hosted FastAPI Bridge"
spec_source: "docs/specs/sveltekit-frontend-and-fastapi-bridge-spec.md"
status: pending-approval
created_at: 2026-11-06
disciplines:
  - codebase-design
  - domain-modeling
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

# Implementation Blueprint: SvelteKit 5 Frontend & Co-Hosted FastAPI Bridge

## 1. Objectives & Executive Summary

This blueprint translates the [SvelteKit 5 Frontend & Co-Hosted FastAPI Bridge Specification](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/docs/specs/sveltekit-frontend-and-fastapi-bridge-spec.md) into **9 sequential, vertical tracer-bullet slices**. Together, these slices deliver a local-first web application co-hosted inside the single `job-scanner` Python process on port `8000` (`UI_PORT` / `--port`) that enables interactive job triage with **Match Explainability** and persistent **Triage Status** (`new | saved | applied`), 1-click **Reactive Dismissals** (`job`, `company`, `source`) with instant Undo and Audit/Restore, no-YAML editing of **Search Profiles** and **Pre-Persistence Guardrails** via **Config Round-Trip Hot-Reload** and **Zero-Network Config Preview**, a visual `serpapi_google` query builder and **Company Registry** manager with **Wildcard Source Rollup** health monitoring, and an **Alert-Safe Scan Trigger** that preserves `SOL-001` **Digest Retry** (`needs_alert`) invariants.

### User Story Traceability Matrix (US-1 through US-32)

| Slice | Target Seam | User Stories Covered | Summary of Deliverable |
| :--- | :--- | :--- | :--- |
| **Slice 1** | Seam 1 (Persistence & Migration) | `US-6`, `US-7`, `US-8` | Idempotent SQLite migration of 8 `jobs` columns + indexes, `Job` Match Explainability & `user_status` fields, `upsert()` status preservation, and `reevaluate_stored_jobs()`. |
| **Slice 2** | Seam 1 & Seam 3 (Pipeline) | `US-6`, `US-8`, `US-31`, `US-32` | Reorder `ScanPipeline._process_source()` so `KeywordMatcher.evaluate()` runs before persistence, refresh unchanged job match metadata, track `is_running` / `last_run_result`, and add `send_notifications: bool = True` to `run_once()`. |
| **Slice 3** | Seam 1 (Persistence Queries) | `US-1`, `US-2`, `US-3`, `US-4`, `US-5`, `US-10`, `US-11`, `US-12`, `US-13`, `US-14` | `JobRepository.query_jobs()` with multi-field filtering, sorting, pagination, `Alert Status` join (`alerted` vs. `pending_alert`), and non-destructive query-time exclusion of active `dismissed_items`. |
| **Slice 4** | Seam 2 (Config Round-Trip) | `US-18`, `US-20`, `US-21`, `US-22`, `US-23`, `US-24`, `US-25`, `US-30` | `ConfigManager` using `ruamel.yaml` to patch `config.yaml` preserving comments and `identifier: "*"` Wildcard Sources, migrate `search_criteria` -> `search_profiles`, atomic swap, `CompanyRegistry` write helpers, and lock-synchronized hot-reload. |
| **Slice 5** | Seam 3 (FastAPI Bridge) | `US-1`–`US-32` (Backend Contracts) | `app/api/` FastAPI application factory and all 16 REST endpoints (`/api/jobs`, `/api/dismissals`, `/api/config`, `/api/config/preview`, `/api/registries`, `/api/sources`, `/api/sources/test-query`, `/api/scan/*`) verified via `TestClient`. |
| **Slice 6** | Seam 3 (Co-Hosting & Lifespan) | `US-31`, `US-32` (Runtime & Docker) | Wire Uvicorn + FastAPI lifespan (`SchedulerService` + startup `reevaluate_stored_jobs`) into `app/main.py` with `--port`/`UI_PORT` and `--no-ui`, mount SPA static fallback, and add multi-stage Node build to `Dockerfile`. |
| **Slice 7** | Seam 4 (SvelteKit & Pure TS) | `US-16`, `US-18`, `US-25`, `US-28` | Bootstrap `frontend/` (`@sveltejs/adapter-static`, Svelte 5 runes, Tailwind CSS) and build Vitest-verified pure TS modules (`query-builder.ts`, `profile-validator.ts`, `guardrail-packs.ts`). |
| **Slice 8** | Seam 4 / UI (Triage & Control) | `US-1`–`US-14`, `US-31`, `US-32` | App shell with live **Scan Control Center** + `/` **Master-Detail Job Triage Feed**, "Why This Matched" card, `new | saved | applied` workflow pills, 1-click Reactive Dismissal split-button, 5-second Undo toast, and Dismissals Audit/Restore drawer. |
| **Slice 9** | Seam 4 / UI (Studios & Wizard) | `US-15`–`US-30` | `/profiles` (**Visual Search Profiles Studio** + **Zero-Network Config Preview**), `/guardrails` (**Pre-Persistence Guardrails Editor** + Preset Industry Block Packs), and `/sources` (**Wildcard Source Rollup** health dashboard, Visual Query Builder, and Registry Manager). |

---

## 2. Affected Areas & Architectural Guardrails

### 2.1 Affected Modules, Tables & Components
- **Persistence Layer (`app/persistence/schema.py`, `app/persistence/repositories.py`, `app/persistence/__init__.py`)**:
  - `jobs` table (`JobModel`) extended via `_JOBS_MIGRATION_COLUMNS` with `is_match`, `primary_profile`, `matched_profiles`, `affinity_score`, `score_breakdown`, `match_snippets`, `matched_fields`, and `user_status`, plus indexes `idx_jobs_match_score` and `idx_jobs_user_status`.
  - `sources` table (`SourceStatusModel` & `SourceRepository`) already keyed by composite primary key `(source_type, source_identifier)` with `_ensure_sources_primary_key(engine)` rebuild migration in `create_schema(engine)`.
  - `JobRepository` extended with `user_status` preservation in `upsert()`, `update_last_seen_and_match()`, `update_user_status()`, `query_jobs()`, `get_triage_by_key()`, and module-level `reevaluate_stored_jobs()`.
  - `DismissedItemRepository` extended with `count_matching_jobs()`.
- **Domain & Matching Layer (`app/domain/models.py`, `app/pipeline/runner.py`, `app/scheduler/service.py`)**:
  - `Job` domain model extended with Match Explainability fields and `user_status: TriageStatus`.
  - `ScanPipeline` updated to evaluate `KeywordMatcher.evaluate()` before persistence, expose `is_running` and `last_run_result`, and support `run_once(send_notifications: bool = True)`.
  - `SchedulerService` extended with `reschedule(interval_seconds: int)`.
- **Configuration & Registry Management (`app/config/manager.py`, `app/adapters/registry.py`)**:
  - New `ConfigManager` encapsulating `ruamel.yaml` round-trip AST loading, partial section patching, legacy `search_criteria` -> `search_profiles` migration, Pydantic validation, atomic file replacement, and lock-synchronized runtime hot-reload.
  - `CompanyRegistry` extended with atomic JSON write methods (`save_companies()`, `add_company()`).
- **FastAPI REST Bridge (`app/api/`, `app/main.py`)**:
  - New `app/api/` package (`app.py`, `schemas.py`, `dependencies.py`, and route modules `jobs.py`, `dismissals.py`, `config.py`, `sources.py`, `scan.py`).
  - `app/main.py` updated with `--port` and `--no-ui` flags, startup re-evaluation pass, and Uvicorn lifespan integration.
- **SvelteKit 5 Frontend (`frontend/`, `Dockerfile`, `pyproject.toml`)**:
  - Static SvelteKit 5 SPA (`frontend/src/lib/` and `frontend/src/routes/`) compiled to `frontend/build/` and served by FastAPI.

### 2.2 Codebase Friction Log Guardrails (Mandatory Implementation Rules)
1. **`SourceStatusModel` Composite PK Already Present**: `app/persistence/schema.py` and `app/persistence/repositories.py` already implement the `(source_type, source_identifier)` composite primary key and `_ensure_sources_primary_key(engine)`. Do not duplicate or rewrite this migration; build `GET /api/sources` **Wildcard Source Rollup** aggregation directly on `SourceRepository.get_all()`.
2. **`JobRepository.upsert()` `session.merge()` Overwrite Trap**: `JobNormalizer.normalize()` constructs a fresh `Job` domain object (which defaults `user_status` to `"new"`). Because `JobRepository.upsert()` calls `self.session.merge(JobModel.from_domain(job))`, merging directly would overwrite an existing row's `'saved'` or `'applied'` status back to `'new'` whenever `content_hash` changes. `JobRepository.upsert()` **must** inspect `existing = self.session.get(JobModel, job.job_key)` prior to `merge()` and preserve `existing.user_status` when present.
3. **Unchanged Jobs (`should_upsert=False`) Match Metadata Refresh**: In `ScanPipeline._process_source()`, when a job's content hash has not changed (`norm_result.should_upsert is False`), calling only `update_last_seen()` would discard the freshly computed `MatchResult`. Replace that call in `_process_source()` with `job_repo.update_last_seen_and_match(norm_result.job, scan_timestamp)` so `last_seen_at` and the 7 Match Explainability columns stay synchronized on every scan without resetting `first_seen_at` or `user_status` or triggering duplicate notifications.
4. **`MatchResult.matched_fields` Set-to-List JSON Serialization**: `MatchResult.matched_fields` is typed as `Dict[str, Set[str]]`. SQLite's `JSON` column serializer raises `TypeError: Object of type set is not JSON serializable` if sets are passed through. `Job.matched_fields` must be typed as `Dict[str, List[str]]` with a Pydantic `@field_validator("matched_fields", mode="before")` that coerces any `set` or sequence values into `sorted(list(v))` (dropping empty keys or normalizing cleanly).

---

## 3. Sequence-Split Vertical Implementation Slices

- [ ] **Slice 1: Persistence Schema Migration, Match Explainability & Startup Re-Evaluation (Seam 1)**
  - **Slice Goal & User Stories Addressed**:
    Extend the SQLite `jobs` schema and `Job` domain model with the 7 **Match Explainability** fields and **Triage Status** (`user_status`), guard `JobRepository.upsert()` against overwriting `user_status` on `content_hash` changes, and provide `reevaluate_stored_jobs()` to backfill legacy rows (`is_match IS NULL`) on startup and recompute all stored jobs when **Search Profiles** change.
    - *User Stories*: `US-6`, `US-7`, `US-8`
  - **Target Seam & Verification Command**:
    - **Seam 1**: `create_schema(engine)`, `Job`, `JobRepository`, and `reevaluate_stored_jobs(session, keyword_matcher, *, only_unscored=False)`
    - **Command**:
      ```bash
      uv run pytest tests/test_persistence.py -k "migrat or explainability or user_status or reevaluate" --no-cov -v
      ```
  - **Files to Create or Modify**:
    - Modify `app/domain/models.py`
    - Modify `app/persistence/schema.py`
    - Modify `app/persistence/repositories.py`
    - Modify `app/persistence/__init__.py`
    - Modify `tests/test_persistence.py`
  - **Public Interface / Seam Signatures**:
    ```python
    # app/domain/models.py
    class TriageStatus(str, Enum):
        NEW = "new"
        SAVED = "saved"
        APPLIED = "applied"

    class Job(BaseModel):
        # ... existing fields ...
        is_match: Optional[bool] = Field(None, description="Whether job matched any active Search Profile")
        primary_profile: Optional[str] = Field(None, description="Winning Search Profile name")
        matched_profiles: List[str] = Field(default_factory=list, description="All matching Search Profile names by score desc")
        affinity_score: float = Field(0.0, description="Weighted affinity score of primary profile")
        score_breakdown: Dict[str, float] = Field(default_factory=dict, description="Per-term points contributing to affinity_score")
        match_snippets: List[str] = Field(default_factory=list, description="Extracted description excerpts with matched keywords")
        matched_fields: Dict[str, List[str]] = Field(default_factory=dict, description="Field name to sorted list of matched terms")
        user_status: TriageStatus = Field(TriageStatus.NEW, description="User triage workflow state")

        def apply_match_result(self, match_result: Any) -> "Job": ...

    # app/persistence/repositories.py
    class JobRepository:
        def upsert(self, job: Job, *, preserve_user_status: bool = True) -> Job: ...
        def update_last_seen_and_match(self, job: Job, timestamp: datetime) -> None: ...
        def update_user_status(self, job_key: str, user_status: str | TriageStatus) -> Job: ...

    def reevaluate_stored_jobs(
        session: Session,
        keyword_matcher: Any,
        *,
        only_unscored: bool = False,
    ) -> int: ...
    ```
  - **Step-by-Step Implementation Instructions**:
    1. **Write Failing Tests First (`tests/test_persistence.py`)**:
       - Extend `TestDatabaseInitialization.test_create_schema_migrates_missing_job_columns` to assert that a legacy v1.0 `jobs` table receives all 8 new columns (`is_match`, `primary_profile`, `matched_profiles`, `affinity_score`, `score_breakdown`, `match_snippets`, `matched_fields`, `user_status`) and indexes (`idx_jobs_match_score`, `idx_jobs_user_status`) idempotently, with existing rows defaulting `user_status` to `'new'` and `affinity_score` to `0.0`.
       - Add `TestJobRepository.test_upsert_persists_match_explainability_and_normalizes_matched_fields_sets` verifying that passing a `MatchResult` (whose `matched_fields` contains Python `set` values) into `job.apply_match_result(match_result)` and `repo.upsert(job)` round-trips cleanly through SQLite JSON columns.
       - Add `TestJobRepository.test_upsert_preserves_saved_and_applied_user_status_across_content_hash_update` verifying that updating a job to `user_status="saved"` via `repo.update_user_status(job_key, "saved")` and then calling `repo.upsert(updated_job_with_new_content_hash)` retains `user_status == TriageStatus.SAVED`.
       - Add `TestJobRepository.test_reevaluate_stored_jobs_backfills_unscored_and_updates_on_profile_change` verifying `reevaluate_stored_jobs(..., only_unscored=True)` scores rows where `is_match IS NULL` without touching already-scored rows, and `only_unscored=False` updates all rows when `KeywordMatcher` profiles change while preserving `user_status`, `first_seen_at`, and `last_seen_at`.
    2. **Update Domain Model (`app/domain/models.py`)**:
       - Define `TriageStatus(str, Enum)` (`NEW = "new"`, `SAVED = "saved"`, `APPLIED = "applied"`).
       - Add the 8 fields to `Job` with a `@field_validator("matched_fields", mode="before")` that converts `{k: sorted(v) for k, v in val.items() if v}` so `set[str]` values from `MatchResult` never fail JSON encoding, and a `@field_validator("user_status", mode="before")` that defaults `None` to `TriageStatus.NEW`.
       - Add `Job.apply_match_result(self, match_result)` helper to copy `is_match`, `primary_profile`, `matched_profiles`, `affinity_score`, `score_breakdown`, `snippets -> match_snippets`, and normalized `matched_fields` onto `self`.
    3. **Update ORM Schema & Migration (`app/persistence/schema.py`)**:
       - Add the 8 mapped columns to `JobModel`, update `JobModel.to_domain()` and `JobModel.from_domain()`, add `Index("idx_jobs_match_score", "is_match", "affinity_score")` and `Index("idx_jobs_user_status", "user_status")` to `JobModel.__table_args__`.
       - Add the 8 columns to `_JOBS_MIGRATION_COLUMNS` and ensure `_ensure_table_columns(engine)` also runs `CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs (is_match, affinity_score)` and `CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs (user_status)` inside `with engine.begin() as conn:`.
    4. **Implement Repository Methods (`app/persistence/repositories.py`)**:
       - In `JobRepository.upsert()`, check `existing = self.session.get(JobModel, job.job_key)`. If `preserve_user_status and existing is not None and existing.user_status`: set `job_to_merge.user_status = existing.user_status` before `self.session.merge()`.
       - Implement `update_last_seen_and_match(self, job: Job, timestamp: datetime) -> None` and `update_user_status(self, job_key: str, user_status: str | TriageStatus) -> Job` (raising `RecordNotFoundError` if `job_key` does not exist).
       - Implement `reevaluate_stored_jobs(session, keyword_matcher, *, only_unscored: bool = False) -> int` using `MatchableText.from_job(job)` and `keyword_matcher.evaluate(job, matchable_text)`.
  - **Verification Gate Criteria**:
    - `uv run pytest tests/test_persistence.py --no-cov -q` passes with all new migration, status-preservation, and re-evaluation tests green.

---

- [ ] **Slice 2: Pipeline Evaluation-Before-Upsert Ordering & Alert-Safe Scan Execution (Seam 1 & Seam 3)**
  - **Slice Goal & User Stories Addressed**:
    Reorder `ScanPipeline._process_source()` so `KeywordMatcher.evaluate()` runs **before** `job_repo.upsert()`, persist Match Explainability on both new/changed and unchanged jobs, expose `pipeline.is_running` and `pipeline.last_run_result`, and add `send_notifications: bool = True` to `ScanPipeline.run_once()` so UI-triggered scans (`send_notifications=False`) persist and score jobs while preserving `SOL-001` **Digest Retry** (`needs_alert` / `CandidateMatch.is_retry`) for the next scheduled digest run.
    - *User Stories*: `US-6`, `US-8`, `US-31`, `US-32`
  - **Target Seam & Verification Command**:
    - **Seam 1 & Seam 3**: `ScanPipeline.run_once(send_notifications: bool = True) -> PipelineRunResult`, `ScanPipeline.is_running`, `ScanPipeline.last_run_result`
    - **Command**:
      ```bash
      uv run pytest tests/test_pipeline.py --no-cov -v
      ```
  - **Files to Create or Modify**:
    - Modify `app/pipeline/runner.py`
    - Modify `tests/test_pipeline.py`
  - **Public Interface / Seam Signatures**:
    ```python
    # app/pipeline/runner.py
    class ScanPipeline:
        last_run_result: Optional[PipelineRunResult]

        @property
        def is_running(self) -> bool:
            """Return True if a pipeline scan currently holds the execution lock."""
            return self._lock.locked()

        def run_once(self, *, send_notifications: bool = True) -> PipelineRunResult: ...
    ```
  - **Step-by-Step Implementation Instructions**:
    1. **Write Failing Tests First (`tests/test_pipeline.py`)**:
       - Add `test_run_once_persists_match_explainability_on_jobs_table` verifying that after `pipeline.run_once()`, querying `JobRepository(session).get_by_key(job_key)` returns a `Job` with `is_match=True`, `primary_profile`, `affinity_score > 0`, non-empty `score_breakdown`, `match_snippets`, and `matched_fields`.
       - Add `test_run_once_preserves_user_status_when_job_content_changes` verifying that when a persisted job has `user_status="applied"` and the adapter returns updated description text on a subsequent scan, the updated `Job` in SQLite has the new `content_hash` and updated `affinity_score` while retaining `user_status == TriageStatus.APPLIED`.
       - Add `test_run_once_alert_safe_mode_skips_notifications_and_preserves_digest_retry` verifying that calling `pipeline.run_once(send_notifications=False)`:
         1. Persists and matches jobs (`result.total_matched > 0`, `result.total_notified == 0`, `result.alerts_sent == 0`).
         2. Does **not** call `notification_service.send_digest()` or `send_notifications()`, and writes zero rows to `alerts_sent`.
         3. On the very next call to `pipeline.run_once(send_notifications=True)` with the exact same unchanged jobs from the adapter, `CandidateMatch.is_retry` (`needs_alert`) is `True` and `notification_service.send_digest()` dispatches the alert for those jobs.
       - Add `test_pipeline_tracks_last_run_result_and_is_running_property` verifying `pipeline.last_run_result` is populated upon run completion and `pipeline.is_running` reflects `self._lock.locked()`.
    2. **Update `ScanPipeline` (`app/pipeline/runner.py`)**:
       - Initialize `self.last_run_result: Optional[PipelineRunResult] = None` in `ScanPipeline.__init__()`.
       - Add `@property def is_running(self) -> bool: return self._lock.locked()`.
       - Update `run_once(self, *, send_notifications: bool = True) -> PipelineRunResult` (keeping `pipeline.run_once()` zero-arg compatible for `SchedulerService` and all existing callers).
       - In `_process_source(self, source_config, scan_timestamp, run_id, *, send_notifications: bool = True)`:
         - After `filtering_service.evaluate_job()` passes, call `match_result = self.keyword_matcher.evaluate(norm_result.job, norm_result.matchable_text)` and `norm_result.job.apply_match_result(match_result)`.
         - Then persist: if `norm_result.should_upsert`, call `job_repo.upsert(norm_result.job)`; else call `job_repo.update_last_seen_and_match(norm_result.job, scan_timestamp)`.
         - When `match_result.is_match` and (`norm_result.should_re_match` or `is_retry`), increment `stats.matched_count` and only append to `matches_to_notify` if `send_notifications and candidate.should_notify`.
       - Store `self.last_run_result = result` before returning in `run_once()` (when not `skipped`).
  - **Verification Gate Criteria**:
    - `uv run pytest tests/test_pipeline.py --no-cov -q` passes with all existing and new pipeline tests green.

---

- [ ] **Slice 3: Query-Time Reactive Dismissal Filtering & Triage Query Engine (Seam 1)**
  - **Slice Goal & User Stories Addressed**:
    Build the SQLite triage query engine on `JobRepository` and `DismissedItemRepository` supporting multi-field filtering (`match_mode`, `profile`, `source_type`, `source_identifier`, `is_remote`, `user_status`, `alert_status`, free-text `q`), sorting (`affinity_score`, `posted_at`, `first_seen_at`), pagination, `Alert Status` computation (`alerted` vs. `pending_alert` via `alerts_sent`), and query-time exclusion of active `job`, `company`, and `source` **Reactive Dismissals** without deleting rows from `jobs`.
    - *User Stories*: `US-1`, `US-2`, `US-3`, `US-4`, `US-5`, `US-10`, `US-11`, `US-12`, `US-13`, `US-14`
  - **Target Seam & Verification Command**:
    - **Seam 1**: `JobRepository.query_jobs(filters: JobQueryFilters) -> JobQueryPage`, `JobRepository.get_triage_by_key(job_key: str) -> Optional[JobTriageRecord]`, `DismissedItemRepository.count_matching_jobs(scope, item_key) -> int`
    - **Command**:
      ```bash
      uv run pytest tests/test_persistence.py -k "query_jobs or triage or dismiss" --no-cov -v
      ```
  - **Files to Create or Modify**:
    - Modify `app/persistence/repositories.py`
    - Modify `app/persistence/__init__.py`
    - Modify `tests/test_persistence.py`
  - **Public Interface / Seam Signatures**:
    ```python
    # app/persistence/repositories.py
    @dataclass
    class JobQueryFilters:
        match_mode: str = "matched"  # "matched" | "all" | "unmatched"
        profile: Optional[str] = None
        source_type: Optional[str] = None
        source_identifier: Optional[str] = None
        is_remote: Optional[bool] = None
        user_status: Optional[str] = None  # "new" | "saved" | "applied"
        alert_status: Optional[str] = None  # "alerted" | "pending_alert"
        q: Optional[str] = None
        sort_by: str = "affinity_score"  # "affinity_score" | "posted_at" | "first_seen_at"
        sort_order: str = "desc"  # "desc" | "asc"
        limit: int = 50
        offset: int = 0
        include_dismissed: bool = False

    @dataclass
    class JobTriageRecord:
        job: Job
        alert_status: str  # "alerted" | "pending_alert" | "none"
        alert_sent_at: Optional[datetime] = None

    @dataclass
    class JobQueryPage:
        items: List[JobTriageRecord]
        total: int
        limit: int
        offset: int
        counts: Dict[str, int]  # {"matched": int, "saved": int, "applied": int, "pending_alert": int}
    ```
  - **Step-by-Step Implementation Instructions**:
    1. **Write Failing Tests First (`tests/test_persistence.py`)**:
       - Add `TestJobTriageQueries` in `tests/test_persistence.py` testing:
         - Default `query_jobs(JobQueryFilters())` returns only `is_match == True` jobs sorted by `affinity_score` descending, with `counts` accurately reflecting `matched`, `saved`, `applied`, and `pending_alert` totals across non-dismissed jobs.
         - Filtering by `match_mode="all"` and `match_mode="unmatched"`, `profile` (matching either `primary_profile` or any entry in `matched_profiles`), `is_remote`, `user_status`, `source_type`, and free-text `q` (case-insensitive substring match on `title`, `company`, or `description`).
         - `alert_status` derivation: a matching job with a row in `alerts_sent` for `(job.job_key, job.content_hash)` has `alert_status == "alerted"` and `alert_sent_at` populated; a matching job with no `alerts_sent` row for its current `content_hash` has `alert_status == "pending_alert"`; a non-matching job has `alert_status == "none"`.
         - Query-time dismissal filtering across all 3 scopes: dismissing a job (`scope="job"`), company (`scope="company"`), or source (`scope="source"`) immediately excludes matching rows from `query_jobs()` and decrements `counts` while keeping the rows intact in `jobs`; calling `DismissedItemRepository.undismiss()` immediately restores those jobs in `query_jobs()` with their original `user_status` and `first_seen_at`.
         - `DismissedItemRepository.count_matching_jobs(scope, item_key)` returns the exact count of stored `jobs` rows affected by that dismissal key.
    2. **Implement Query Engine (`app/persistence/repositories.py`)**:
       - Left-join `JobModel` with `AlertRecordModel` on `and_(AlertRecordModel.job_key == JobModel.job_key, AlertRecordModel.version_hash == JobModel.content_hash)` to derive `alert_status` (`"alerted"` when `AlertRecordModel.sent_at.is_not(None)`, `"pending_alert"` when `JobModel.is_match.is_(True)` and `AlertRecordModel.sent_at.is_(None)`, else `"none"`).
       - Apply query-time exclusion predicates when `not filters.include_dismissed` using `~func.lower(JobModel.job_key).in_(select(DismissedItemModel.item_key).where(DismissedItemModel.scope == "job"))`, `~func.lower(JobModel.company).in_(...)`, and `~func.lower(JobModel.source_identifier).in_(...)`.
       - Implement `get_triage_by_key(self, job_key: str) -> Optional[JobTriageRecord]` and `DismissedItemRepository.count_matching_jobs(self, scope: str | DismissalScope, item_key: str) -> int`.
  - **Verification Gate Criteria**:
    - `uv run pytest tests/test_persistence.py -k "TestJobTriageQueries or TestDismissedItemRepository" --no-cov -q` passes 100%.

---

- [ ] **Slice 4: `ruamel.yaml` Config Round-Trip Hot-Reload & Registry Persistence (Seam 2)**
  - **Slice Goal & User Stories Addressed**:
    Build `ConfigManager` (`app/config/manager.py`) using `ruamel.yaml` in round-trip mode to patch `config.yaml` in place while preserving YAML comments and unexpanded `identifier: "*"` **Wildcard Sources**, automatically migrate legacy `search_criteria` to `search_profiles` on save, validate candidate configs via `AppConfig.model_validate()`, swap files atomically via `.tmp` + `os.replace()`, add `CompanyRegistry` persistence helpers (`save_companies`, `add_company`), add `SchedulerService.reschedule()`, and hot-reload runtime state under `ScanPipeline._lock`.
    - *User Stories*: `US-18`, `US-20`, `US-21`, `US-22`, `US-23`, `US-24`, `US-25`, `US-30`
  - **Target Seam & Verification Command**:
    - **Seam 2**: `ConfigManager`, `SchedulerService.reschedule()`, `CompanyRegistry.save_companies()`, `CompanyRegistry.add_company()`
    - **Command**:
      ```bash
      uv run pytest tests/test_config_manager.py tests/test_registry.py --no-cov -v
      ```
  - **Files to Create or Modify**:
    - Modify `pyproject.toml` (add `"ruamel.yaml>=0.18.0"`)
    - Create `app/config/manager.py`
    - Modify `app/config/__init__.py`
    - Modify `app/adapters/registry.py`
    - Modify `app/scheduler/service.py`
    - Create `tests/test_config_manager.py`
  - **Public Interface / Seam Signatures**:
    ```python
    # app/config/manager.py
    class ConfigUpdatePayload(BaseModel):
        search_profiles: Optional[List[SearchProfile]] = None
        filtering: Optional[FilteringRules] = None
        sources: Optional[List[SourceConfig]] = None
        scan_interval: Optional[str] = None
        email: Optional[EmailConfig] = None
        logging: Optional[LoggingConfig] = None
        advanced: Optional[AdvancedConfig] = None

    @dataclass
    class ConfigReloadResult:
        raw_config: AppConfig
        expanded_config: AppConfig
        reevaluated_jobs_count: int = 0

    class ConfigManager:
        def __init__(
            self,
            config_path: Path,
            *,
            pipeline: Optional[Any] = None,
            scheduler_service: Optional[Any] = None,
            registry_dir: Path = Path("data/registries"),
        ) -> None: ...

        def get_raw_config(self) -> AppConfig: ...
        def get_expanded_config(self) -> AppConfig: ...
        def update_config(self, payload: ConfigUpdatePayload) -> ConfigReloadResult: ...
        def reload_registries(self) -> AppConfig: ...
    ```
  - **Step-by-Step Implementation Instructions**:
    1. **Add Dependency**: Add `"ruamel.yaml>=0.18.0"` to `dependencies` in `pyproject.toml` and run `uv sync`.
    2. **Write Failing Tests First (`tests/test_config_manager.py`)**:
       - `test_update_search_profiles_preserves_yaml_comments_and_wildcard_sources`: Create a temp `config.yaml` containing `# Top header comment`, an `identifier: "*"` wildcard source with inline comments, and a `# Filtering section comment`. Call `config_manager.update_config(ConfigUpdatePayload(search_profiles=[...]))`. Assert the on-disk `config.yaml` retains `# Top header comment`, `# Filtering section comment`, and `identifier: "*"` (unexpanded), while `config_manager.get_expanded_config().sources` contains the expanded registry companies.
       - `test_saving_search_profiles_migrates_legacy_search_criteria_key`: Start from a legacy `config.yaml` that defines `search_criteria:` and no `search_profiles:`. Call `update_config(ConfigUpdatePayload(search_profiles=[...]))`. Assert `search_criteria` is removed from `config.yaml` and replaced by `search_profiles`.
       - `test_invalid_config_update_raises_configuration_error_and_leaves_disk_and_runtime_untouched`: Pass a profile with a term in both `required_terms` and `exclude_terms` (or `filtering` with `hard_block_years < max_years_experience`). Assert `ConfigurationError` is raised, the file on disk is unchanged, no `.tmp` file remains, and `pipeline.app_config` is unchanged.
       - `test_hot_reload_updates_pipeline_matcher_scheduler_and_reevaluates_stored_jobs`: Wire `ConfigManager` with a real `ScanPipeline`, `SchedulerService`, and SQLite database containing stored jobs. Update `search_profiles` and `scan_interval="30m"`. Verify `pipeline.keyword_matcher.profiles` is updated, `scheduler_service.interval_seconds == 1800`, and stored jobs in SQLite are re-evaluated (`reevaluated_jobs_count > 0`).
       - `test_registry_toggle_and_add_company_persists_json_and_reexpands_wildcards`: Verify `CompanyRegistry.save_companies()` and `CompanyRegistry.add_company()` (rejecting duplicate identifiers case-insensitively) write atomically to ` {ats}_companies.json` and `config_manager.reload_registries()` updates `pipeline.app_config.sources`.
    3. **Implement `SchedulerService.reschedule()` (`app/scheduler/service.py`)**:
       - Add `reschedule(self, interval_seconds: int) -> None` to `SchedulerService` that updates `self.interval_seconds = interval_seconds` and, if `self.scheduler.running`, calls `self.scheduler.reschedule_job("job-scan", trigger=IntervalTrigger(seconds=interval_seconds, timezone=timezone.utc))`.
    4. **Implement `CompanyRegistry` Write Helpers (`app/adapters/registry.py`)**:
       - Add `get_registry_metadata(self, ats_type: str) -> dict`, `save_companies(self, ats_type: str, companies: List[Dict]) -> dict`, and `add_company(self, ats_type: str, identifier: str, name: str, enabled: bool = True) -> dict` using atomic `.tmp` + `os.replace()` and updating `"updated_at"` to current UTC ISO-8601 timestamp.
    5. **Implement `ConfigManager` (`app/config/manager.py`)**:
       - Use `ruamel.yaml.YAML(typ="rt")` to load the raw YAML file into a `CommentedMap`.
       - In `update_config()`, clone/patch the `CommentedMap` keys for non-`None` fields in `ConfigUpdatePayload`. If `payload.search_profiles is not None` and `"search_criteria"` is in the `CommentedMap`, delete `"search_criteria"`.
       - Convert the patched map to plain Python containers and validate via `AppConfig.model_validate()`.
       - Write atomically to `config_path.with_suffix(".yaml.tmp")` and `os.replace()` onto `config_path`.
       - Acquire `pipeline._lock` when `pipeline` is attached, expand wildcards using `CompanyRegistry(self.registry_dir)`, swap `pipeline.app_config` and `pipeline.keyword_matcher`, call `scheduler_service.reschedule()` if `scan_interval` changed, and run `reevaluate_stored_jobs(session, pipeline.keyword_matcher, only_unscored=False)` if `search_profiles` changed.
  - **Verification Gate Criteria**:
    - `uv run pytest tests/test_config_manager.py tests/test_registry.py tests/test_config.py --no-cov -q` passes 100%.

---

- [ ] **Slice 5: FastAPI REST Bridge Across All 6 Feature Domains (Seam 3)**
  - **Slice Goal & User Stories Addressed**:
    Implement the `app/api/` FastAPI application factory and all 16 REST endpoints covering Job Triage (`/api/jobs`), Reactive Dismissals (`/api/dismissals`), Configuration & **Zero-Network Config Preview** (`/api/config`, `/api/config/preview`), Sources, **Wildcard Source Rollups**, SerpAPI Live Query Test, and Company Registries (`/api/sources`, `/api/sources/test-query`, `/api/registries/{ats_type}`), and Scan Control (`/api/scan/status`, `/api/scan/trigger`).
    - *User Stories*: Backend contracts for `US-1` through `US-32`
  - **Target Seam & Verification Command**:
    - **Seam 3**: `create_app(...)` exercised via `fastapi.testclient.TestClient`
    - **Command**:
      ```bash
      uv run pytest tests/test_api.py --no-cov -v
      ```
  - **Files to Create or Modify**:
    - Modify `pyproject.toml` (add `"fastapi>=0.115.0"`, `"uvicorn>=0.32.0"` to `dependencies`; add `"httpx>=0.27.0"` to `[project.optional-dependencies].dev`)
    - Create `app/api/__init__.py`
    - Create `app/api/schemas.py`
    - Create `app/api/dependencies.py`
    - Create `app/api/routes/__init__.py`
    - Create `app/api/routes/jobs.py`
    - Create `app/api/routes/dismissals.py`
    - Create `app/api/routes/config.py`
    - Create `app/api/routes/sources.py`
    - Create `app/api/routes/scan.py`
    - Create `app/api/app.py`
    - Create `tests/test_api.py`
  - **Public Interface / Seam Signatures**:
    ```python
    # app/api/app.py
    def create_app(
        pipeline: ScanPipeline,
        scheduler_service: Optional[SchedulerService] = None,
        config_manager: Optional[ConfigManager] = None,
        static_dir: Optional[Path] = None,
        manage_scheduler_lifespan: bool = False,
    ) -> FastAPI: ...
    ```
  - **Step-by-Step Implementation Instructions**:
    1. **Install Dependencies**: Add `fastapi>=0.115.0`, `uvicorn>=0.32.0`, and `httpx>=0.27.0` (in `dev`) to `pyproject.toml` and run `uv sync --extra dev`.
    2. **Write Failing Integration Tests First (`tests/test_api.py`)**:
       - `test_jobs_triage_list_detail_and_status_transitions`: Seed matched and unmatched jobs and an `alerts_sent` record. Verify `GET /api/jobs` filtering (`match_mode`, `profile`, `is_remote`, `user_status`, `alert_status`, `q`), sorting (`affinity_score`, `posted_at`), `GET /api/jobs/{job_key}`, and `PATCH /api/jobs/{job_key}/status` (`new -> saved -> applied`, plus `404` on unknown key and `422` on invalid status).
       - `test_dismissals_create_query_time_hide_undo_and_list`: Call `POST /api/dismissals` for a `job`, `company`, and `source`. Verify `hidden_job_count` in the `201` response, verify those jobs immediately vanish from `GET /api/jobs`, verify `GET /api/dismissals?scope=company` lists the mute, and verify `DELETE /api/dismissals?scope=company&item_key=...` immediately restores the company's jobs in `GET /api/jobs`.
       - `test_config_get_preview_and_put_hot_reload`: Verify `GET /api/config` returns raw sources (keeping `identifier: "*"`), `search_profiles`, `filtering`, and `env_status` booleans (`serpapi_configured`, `smtp_configured`) without leaking raw secrets. Verify `POST /api/config/preview` performs a zero-network dry-run against stored SQLite jobs and returns `total_evaluated`, `total_matched`, `newly_matched_count`, `dropped_match_count`, `profile_counts`, and `sample_matches`. Verify `PUT /api/config` updates `config.yaml`, re-evaluates stored jobs, and returns `422` on conflicting terms.
       - `test_sources_wildcard_rollup_registries_and_test_query`: Verify `GET /api/sources` returns `explicit_sources` and `wildcard_rollups` (with `total_companies`, `total_enabled`, `healthy_count`, `error_count`, `unscanned_count`, and sanitized `error_message` values). Verify `GET /api/registries/greenhouse`, `PUT /api/registries/greenhouse`, and `POST /api/registries/greenhouse/companies` (including `409 Conflict` on duplicate slug). Verify `POST /api/sources/test-query` returns `400` when `SERPAPI_API_KEY` is unset and returns hydrated/fallback `RawJob` previews when mocked.
       - `test_scan_status_and_alert_safe_trigger_and_conflict`: Verify `GET /api/scan/status` returns `is_running`, `next_run_time`, and `last_run_result` funnel metrics. Verify `POST /api/scan/trigger` defaults to `send_notifications=False`, runs the pipeline without writing to `alerts_sent`, and returns `409 Conflict` when `pipeline._lock` is already held.
    3. **Implement Route Modules (`app/api/routes/*.py`) & App Factory (`app/api/app.py`)**:
       - Ensure all error strings returned by `/api/sources` and `/api/sources/test-query` pass through `BaseAdapter._sanitize_url()`.
       - Map `ConfigurationError` and Pydantic `ValidationError` in `/api/config` and `/api/config/preview` to `422 Unprocessable Entity` with structured `{detail, errors}` payloads.
  - **Verification Gate Criteria**:
    - `uv run pytest tests/test_api.py --no-cov -q` passes 100%.

---

- [ ] **Slice 6: Single-Process Co-Hosting, Daemon Lifespan & Multi-Stage Docker Build (Seam 3)**
  - **Slice Goal & User Stories Addressed**:
    Wire `create_app()` and Uvicorn into `app/main.py` when running in default daemon mode, manage `SchedulerService` inside FastAPI's `lifespan` context manager, execute the startup `reevaluate_stored_jobs(..., only_unscored=True)` pass after `init_database()`, add `--port` / `UI_PORT` and `--no-ui` flags while preserving headless CLI behavior for `--manual-run` and `--dismiss`/`--undismiss`/`--list-dismissed`, serve the static SPA bundle with `index.html` fallback routing, and update `Dockerfile` with a multi-stage Node frontend build.
    - *User Stories*: `US-31`, `US-32` (Co-hosted deployment & CLI parity)
  - **Target Seam & Verification Command**:
    - **Seam 3**: `app/main.py` CLI entry point & FastAPI lifespan + SPA static serving in `create_app()`
    - **Command**:
      ```bash
      uv run pytest tests/test_main.py tests/test_api.py -k "lifespan or static or cli or ui" --no-cov -v
      ```
  - **Files to Create or Modify**:
    - Modify `app/main.py`
    - Modify `app/api/app.py`
    - Modify `Dockerfile`
    - Modify `tests/test_main.py`
    - Modify `tests/test_api.py`
  - **Public Interface / Seam Signatures**:
    ```python
    # app/main.py CLI arguments added to ArgumentParser:
    # --port (int, default resolved from UI_PORT env var or 8000)
    # --host (str, default resolved from UI_HOST env var or "127.0.0.1")
    # --no-ui (action="store_true", runs headless SchedulerService without Uvicorn)
    ```
  - **Step-by-Step Implementation Instructions**:
    1. **Write Failing Tests First (`tests/test_main.py` & `tests/test_api.py`)**:
       - In `tests/test_api.py`, add `test_static_spa_serving_and_client_route_fallback` using a `tmp_path` directory containing `index.html` and `_app/immutable/bundle.js` passed as `static_dir` to `create_app()`. Verify `GET /_app/immutable/bundle.js` serves the asset, `GET /profiles` and `GET /sources` serve `index.html` (SPA client-route fallback), and `GET /api/unknown` returns `404` JSON rather than `index.html`.
       - In `tests/test_api.py`, add `test_fastapi_lifespan_starts_and_stops_scheduler` verifying that entering `with TestClient(app):` when `manage_scheduler_lifespan=True` calls `scheduler_service.start()` on startup and `scheduler_service.shutdown(wait=False)` on exit.
       - In `tests/test_main.py`, add tests verifying:
         - Default daemon mode invokes `uvicorn.run` on the configured `--port` / `UI_PORT` and runs the startup `reevaluate_stored_jobs(..., only_unscored=True)` pass.
         - `--no-ui` runs the headless `SchedulerService` loop without invoking `uvicorn.run`.
         - `--manual-run` and `--dismiss` / `--undismiss` / `--list-dismissed` bypass `uvicorn.run` and exit directly.
    2. **Implement Lifespan & SPA Fallback (`app/api/app.py`)**:
       - Configure `lifespan` on `FastAPI` so when `manage_scheduler_lifespan=True` and `scheduler_service` is provided, it starts `scheduler_service.start()` on startup and calls `scheduler_service.shutdown(wait=False)` on shutdown.
       - When `static_dir` exists and contains `index.html`, mount static files and register a fallback route for non-`/api/` paths returning `FileResponse(static_dir / "index.html")`.
    3. **Wire `app/main.py` & Multi-Stage `Dockerfile`**:
       - Add `--port`, `--host`, and `--no-ui` to `app/main.py`.
       - After `init_database(env_config.database_url)` and `KeywordMatcher` initialization in `app/main.py`, run `with get_session() as session: reevaluate_stored_jobs(session, keyword_matcher, only_unscored=True)`.
       - Instantiate `ConfigManager(args.config, pipeline=pipeline, scheduler_service=scheduler_service)`.
       - When not `args.manual_run` and not `args.no_ui`, create the FastAPI app via `create_app(..., manage_scheduler_lifespan=True)` and run `uvicorn.run(fastapi_app, host=host, port=port, log_config=None)`.
       - Update `Dockerfile` with `FROM node:22-slim AS frontend-builder` to build `/frontend/build` and copy it to `/app/frontend/build` in the Python runtime image with `ENV UI_HOST=0.0.0.0 UI_PORT=8000` and `EXPOSE 8000`.
  - **Verification Gate Criteria**:
    - `uv run pytest tests/test_main.py tests/test_api.py --no-cov -q` passes 100%.

---

- [ ] **Slice 7: SvelteKit 5 SPA Foundation & Pure TypeScript Domain Engines (Seam 4)**
  - **Slice Goal & User Stories Addressed**:
    Scaffold the `frontend/` SvelteKit 5 application (`@sveltejs/adapter-static` with `fallback: "index.html"`, TypeScript, Tailwind CSS, Vitest) and implement the typed API client plus the 3 pure TypeScript domain engines tested at **Seam 4**:
    1. `query-builder.ts` — Bidirectional `serpapi_google` query builder & parser (`site:... OR ... "exact phrase" (term1 OR term2) -excluded`).
    2. `profile-validator.ts` — Real-time Search Profile conflict & completeness validator (`required_terms` vs. `exclude_terms`, `keyword_groups` vs. `exclude_terms`, `affinity_weights` vs. `exclude_terms`, empty rules check, duplicate profile names).
    3. `guardrail-packs.ts` — Curated Preset Industry Block Packs (`Crypto / Web3`, `Sports Betting & iGaming`, `Defense & Clearance Required`, `Staffing & Body Shops`) and idempotent list merger.
    - *User Stories*: `US-16`, `US-18`, `US-25`, `US-28`
  - **Target Seam & Verification Command**:
    - **Seam 4**: `frontend/src/lib/query-builder.ts`, `frontend/src/lib/profile-validator.ts`, `frontend/src/lib/guardrail-packs.ts`
    - **Command**:
      ```bash
      npm --prefix frontend test -- --run
      ```
  - **Files to Create or Modify**:
    - Create `frontend/package.json`
    - Create `frontend/svelte.config.js`
    - Create `frontend/vite.config.ts`
    - Create `frontend/tsconfig.json`
    - Create `frontend/src/app.html`
    - Create `frontend/src/app.css`
    - Create `frontend/src/routes/+layout.ts` (`export const ssr = false; export const prerender = false;`)
    - Create `frontend/src/lib/types.ts`
    - Create `frontend/src/lib/api.ts`
    - Create `frontend/src/lib/query-builder.ts`
    - Create `frontend/src/lib/profile-validator.ts`
    - Create `frontend/src/lib/guardrail-packs.ts`
    - Create `frontend/src/lib/__tests__/query-builder.test.ts`
    - Create `frontend/src/lib/__tests__/profile-validator.test.ts`
    - Create `frontend/src/lib/__tests__/guardrail-packs.test.ts`
  - **Public Interface / Seam Signatures**:
    ```typescript
    // frontend/src/lib/query-builder.ts
    export interface SerpQueryState {
      sites: string[];
      exactPhrases: string[];
      orGroups: string[][];
      excludedTerms: string[];
      rawTail: string;
    }
    export function serializeSerpQuery(state: SerpQueryState): string;
    export function parseSerpQuery(query: string): SerpQueryState;

    // frontend/src/lib/profile-validator.ts
    export interface ProfileValidationIssue {
      field: 'name' | 'rules' | 'required_terms' | 'keyword_groups' | 'affinity_weights';
      message: string;
      conflictingTerms?: string[];
      groupIndex?: number;
    }
    export function validateSearchProfile(
      profile: SearchProfileDTO,
      allProfiles?: SearchProfileDTO[]
    ): ProfileValidationIssue[];

    // frontend/src/lib/guardrail-packs.ts
    export interface IndustryBlockPack {
      id: string;
      label: string;
      description: string;
      blockedDomains: string[];
      blockedCompanies: string[];
    }
    export const PRESET_INDUSTRY_PACKS: readonly IndustryBlockPack[];
    export function applyIndustryPack(
      filtering: FilteringRulesDTO,
      packId: string
    ): FilteringRulesDTO;
    ```
  - **Step-by-Step Implementation Instructions**:
    1. **Initialize `frontend/` Project**:
       - Configure `package.json` with Svelte 5 (`svelte@^5.0.0`), `@sveltejs/kit@^2.0.0`, `@sveltejs/adapter-static@^3.0.0`, `@sveltejs/vite-plugin-svelte@^5.0.0`, `tailwindcss`, `vitest`, and `typescript`.
       - Configure `svelte.config.js` with `adapter({ pages: 'build', assets: 'build', fallback: 'index.html', precompress: false, strict: false })`.
       - Run `npm --prefix frontend install`.
    2. **Write Failing Vitest Suites First (`frontend/src/lib/__tests__/*.test.ts`)**:
       - `query-builder.test.ts`: Verify `serializeSerpQuery()` and `parseSerpQuery()` round-trip ATS `site:` groups (`boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`), quoted exact phrases (`"data engineer"`), parenthesized OR groups (`(remote OR distributed)`), negated exclusions (`-senior -"staff engineer"`), and preserve arbitrary unparsed tokens in `rawTail`.
       - `profile-validator.test.ts`: Verify detection of empty `required_terms` + `keyword_groups`, case-insensitive overlap between `required_terms` and `exclude_terms`, overlap between any `keyword_groups[i]` and `exclude_terms`, overlap between `affinity_weights` keys and `exclude_terms`, and duplicate profile names across `allProfiles`.
       - `guardrail-packs.test.ts`: Verify `applyIndustryPack()` merges a preset pack's `blockedDomains` and `blockedCompanies` (normalized lowercase, trimmed) without duplicating already-present entries or mutating the input object.
    3. **Implement Pure TS Modules & Typed API Client (`frontend/src/lib/*.ts`)**:
       - Implement `query-builder.ts`, `profile-validator.ts`, `guardrail-packs.ts`, `types.ts`, and `api.ts` wrapping all 16 FastAPI endpoints.
  - **Verification Gate Criteria**:
    - `npm --prefix frontend test -- --run` passes all unit suites cleanly.

---

- [ ] **Slice 8: Master-Detail Job Triage Feed, Reactive Dismissal Split-Button & Scan Control Bar (Frontend)**
  - **Slice Goal & User Stories Addressed**:
    Build the shared navigation layout (`frontend/src/routes/+layout.svelte`) with the persistent **Scan Control Center** bar (live `is_running` status, `next_run_time`, `last_run_result` pipeline funnel popover/drawer, and **Alert-Safe Scan Trigger** split-button) and the `/` **Master-Detail Job Triage Feed** (`frontend/src/routes/+page.svelte`) featuring filter/sort controls, job cards with **Alert Status** (`alerted` vs. `pending_alert`) and **Triage Status** (`new | saved | applied`), the "Why This Matched" **Match Explainability** breakdown card with highlighted snippets, the 1-click **Reactive Dismissal** split-button (`job`, `company`, `source` scopes with quick-reason chips), the 5-second Undo toast, and the Dismissals Audit & Restore slide-over drawer.
    - *User Stories*: `US-1`, `US-2`, `US-3`, `US-4`, `US-5`, `US-6`, `US-7`, `US-8`, `US-9`, `US-10`, `US-11`, `US-12`, `US-13`, `US-14`, `US-31`, `US-32`
  - **Target Seam & Verification Command**:
    - **Seam 4 & Static Build**: Vitest suite + SvelteKit production static bundle compilation (`frontend/build/index.html`)
    - **Command**:
      ```bash
      npm --prefix frontend test -- --run && npm --prefix frontend run build
      ```
  - **Files to Create or Modify**:
    - Create `frontend/src/routes/+layout.svelte`
    - Create `frontend/src/routes/+page.svelte`
    - Create `frontend/src/lib/components/ScanControlBar.svelte`
    - Create `frontend/src/lib/components/WhyMatchedCard.svelte`
    - Create `frontend/src/lib/components/DismissSplitButton.svelte`
    - Create `frontend/src/lib/components/DismissalsDrawer.svelte`
    - Create `frontend/src/lib/components/UndoToast.svelte`
  - **Public Interface / Seam Signatures**:
    - Route `/`: Master-detail triage workspace backed by `GET /api/jobs`, `PATCH /api/jobs/{job_key}/status`, `POST /api/dismissals`, `DELETE /api/dismissals`, `GET /api/dismissals`, `GET /api/scan/status`, and `POST /api/scan/trigger`.
  - **Step-by-Step Implementation Instructions**:
    1. **Build `ScanControlBar.svelte` & `+layout.svelte`**:
       - Poll `GET /api/scan/status` (every 3s while `is_running` is `True`, every 15s when idle) using Svelte 5 `$state` and `$effect`.
       - Render the pipeline funnel (`total_fetched` -> `total_normalized` -> `total_filtered` with filter reason breakdown -> `total_upserted` -> `total_matched` -> `total_notified` / `alerts_sent` + `total_errors` and duration) and the Alert-Safe "Scan Now" button (`send_notifications = false` by default, with an opt-in checkbox `"Send email notifications for this run"`).
    2. **Build Master-Detail Triage Feed (`+page.svelte`)**:
       - Filter bar with controls for `match_mode` (`Matched Only` default vs. `Include Non-Matching Persisted Jobs`), `profile` dropdown, `user_status` tabs (`All`, `New`, `Saved`, `Applied` with live badge counts), `alert_status` filter (`All`, `Alerted`, `Pending Alert`), `is_remote` toggle, `source_type` filter, free-text search `q`, and sort selector (`Affinity Score`, `Posted Date`, `First Seen`).
       - Master list cards showing title, company, location/remote badges, `primary_profile` pill, `affinity_score` badge, `Alert Status` badge (`alerted` with timestamp tooltip vs. `pending_alert`), and quick `new | saved | applied` pills.
       - Detail pane with `WhyMatchedCard.svelte` displaying `primary_profile`, all `matched_profiles`, `affinity_score`, per-term `score_breakdown` progress bars, `matched_fields` badges, highlighted `match_snippets`, full sanitized description, and "Open Original Posting" external link.
    3. **Build `DismissSplitButton.svelte`, `UndoToast.svelte` & `DismissalsDrawer.svelte`**:
       - 1-click primary action dismisses `scope="job"`; split dropdown offers `Mute Company (<company>)` and `Mute Source (<source_identifier>)` with quick-reason chips (`"Wrong stack"`, `"Too senior"`, `"Location/Comp"`, `"Staffing agency"`, or custom input).
       - On dismissal, immediately remove hidden job(s) from the active feed and show a 5-second `UndoToast` with a 1-click "Undo" button that calls `DELETE /api/dismissals` and reloads the restored job(s) into the feed.
       - Slide-over `DismissalsDrawer` listing all active `dismissed_items` filterable by `scope` and searchable by `item_key`/`reason`, with a "Restore" button per row.
  - **Verification Gate Criteria**:
    - `npm --prefix frontend run build` succeeds and emits `frontend/build/index.html`.

---

- [ ] **Slice 9: Visual Search Profiles Studio, Guardrails Editor & Guided Source Wizard (Frontend + End-to-End Integration)**
  - **Slice Goal & User Stories Addressed**:
    Build the remaining 3 SvelteKit 5 workspaces (`/profiles`, `/guardrails`, `/sources`) and verify the complete co-hosted stack end-to-end:
    - `/profiles`: **Visual Search Profiles Studio** with multi-track CRUD, interactive chip editors (`required_terms`, visual OR-group cards for `keyword_groups`, `exclude_terms`, `boundary_terms`, `contextual_terms`), `affinity_weights` sliders, `title_boost_multiplier` & `min_affinity_score` inputs, live conflict validation via `profile-validator.ts`, **Zero-Network Config Preview** panel (`POST /api/config/preview`), and **Config Round-Trip Hot-Reload** save (`PUT /api/config`).
    - `/guardrails`: **Pre-Persistence Guardrails Editor** covering `AgeFilter`, `LocationFilter`, `RoleFilter`, and `DomainFilter` + 1-click **Preset Industry Block Packs** (`guardrail-packs.ts`).
    - `/sources`: **Source Health Dashboard** with expandable **Wildcard Source Rollup** cards and sanitized error drill-downs (`GET /api/sources`), **Visual `serpapi_google` Query Builder** (`query-builder.ts`) with opt-in live dry-run test (`POST /api/sources/test-query`), and **ATS Company Registry Manager** (`GET/PUT/POST /api/registries/{ats_type}`).
    - *User Stories*: `US-15`, `US-16`, `US-17`, `US-18`, `US-19`, `US-20`, `US-21`, `US-22`, `US-23`, `US-24`, `US-25`, `US-26`, `US-27`, `US-28`, `US-29`, `US-30`
  - **Target Seam & Verification Command**:
    - **Seam 3 & Seam 4**: Full frontend Vitest suite + SvelteKit production static build + full backend `pytest` suite (including `TestClient` serving the compiled `frontend/build` bundle) + `ruff` + `mypy`
    - **Command**:
      ```bash
      npm --prefix frontend test -- --run && npm --prefix frontend run build && uv run pytest --no-cov -v && uv run ruff check app tests && uv run mypy app
      ```
  - **Files to Create or Modify**:
    - Create `frontend/src/routes/profiles/+page.svelte`
    - Create `frontend/src/routes/guardrails/+page.svelte`
    - Create `frontend/src/routes/sources/+page.svelte`
    - Create `frontend/src/lib/components/ChipInput.svelte`
    - Create `frontend/src/lib/components/SerpQueryBuilderModal.svelte`
    - Create `frontend/src/lib/components/RegistryManagerPanel.svelte`
  - **Step-by-Step Implementation Instructions**:
    1. **Build `/profiles` (`frontend/src/routes/profiles/+page.svelte`)**:
       - Render the list of `SearchProfile` tracks with enable/disable toggle, Add Profile, Duplicate Profile, and Delete Profile actions.
       - Render `ChipInput.svelte` editors for `required_terms`, OR-group cards for `keyword_groups`, `exclude_terms`, `boundary_terms`, and key-value chip rows for `contextual_terms`.
       - Render sliders/numeric inputs for `affinity_weights`, `title_boost_multiplier` (`1.0`–`10.0`), and `min_affinity_score`.
       - Run `validateSearchProfile()` reactively via `$derived`; disable Save/Preview and highlight conflicting terms when issues exist.
       - Wire "Dry-Run Preview against Stored Jobs" to `POST /api/config/preview`, displaying `total_evaluated`, `total_matched`, `+newly_matched_count / -dropped_match_count` deltas, per-profile match counts, and top sample matches with score breakdowns.
       - Wire "Save & Hot-Reload Profiles" to `PUT /api/config`, surfacing the `reevaluated_jobs_count` confirmation banner.
    2. **Build `/guardrails` (`frontend/src/routes/guardrails/+page.svelte`)**:
       - Render 4 collapsible cards for `AgeFilter`, `LocationFilter`, `RoleFilter`, and `DomainFilter` bound to `FilteringRules`.
       - Include inline validation that `hard_block_years >= max_years_experience` and `remote_synonyms` is non-empty when `require_remote` is enabled.
       - Render 1-click Preset Industry Block Pack buttons (`PRESET_INDUSTRY_PACKS`) using `applyIndustryPack()`, and wire "Save & Hot-Reload Guardrails" to `PUT /api/config`.
    3. **Build `/sources` (`frontend/src/routes/sources/+page.svelte`)**:
       - Render **Wildcard Source Rollup** cards (`greenhouse`, `lever`, `ashby`) displaying `total_enabled`, `healthy_count`, `error_count`, and `unscanned_count`, expanding on click into a filterable company health table (`All`, `Failing Only`, `Healthy`, `Unscanned`) with sanitized `error_message` and `last_error_at` / `last_success_at` timestamps.
       - Render the Explicit Sources list with enable/disable toggles, Add Source wizard, and `SerpQueryBuilderModal.svelte` for `serpapi_google` sources (using `parseSerpQuery` / `serializeSerpQuery` and "Test Query Live" via `POST /api/sources/test-query`, displaying a clear badge if `env_status.serpapi_configured` is `false`).
       - Render `RegistryManagerPanel.svelte` for browsing, searching, bulk-toggling, and adding `{identifier, name, enabled}` entries to `greenhouse`, `lever`, and `ashby` registries (`GET/PUT/POST /api/registries/{ats_type}`).
    4. **Compile Static Bundle & Run Master Verification Gate**:
       - Run `npm --prefix frontend run build` so `frontend/build/` is populated.
       - Run the full Python test suite (`uv run pytest`), linter (`uv run ruff check app tests`), and type-checker (`uv run mypy app`).
  - **Verification Gate Criteria**:
    - `npm --prefix frontend test -- --run` passes 100%.
    - `npm --prefix frontend run build` produces `frontend/build/index.html`.
    - `uv run pytest --no-cov -q` passes 100% (all 568 baseline tests + all new Seam 1–3 tests).
    - `uv run ruff check app tests` and `uv run mypy app` exit `0`.

---

## 4. Testing Strategy & Seam Summary

Every slice is anchored on one of the **4 confirmed public testing seams**, testing external behavior rather than private implementation details:

1. **Seam 1 — Persistence & Schema Migration (`tests/test_persistence.py`)**:
   - Exercises `create_schema(engine)`, `JobRepository`, `SourceRepository`, `DismissedItemRepository`, `AlertRepository`, and `reevaluate_stored_jobs()` against real SQLite databases (both fresh `:memory:` and legacy on-disk schemas in `tmp_path`).
   - Prior Art: [`tests/test_persistence.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_persistence.py).
2. **Seam 2 — Config Round-Trip & Hot-Reload (`tests/test_config_manager.py`)**:
   - Exercises `ConfigManager.update_config()`, `ConfigManager.reload_registries()`, `CompanyRegistry.save_companies()`, and `SchedulerService.reschedule()` against real `config.yaml` and registry JSON files in `tmp_path`.
   - Prior Art: [`tests/test_config.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_config.py) and [`tests/test_registry.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_registry.py).
3. **Seam 3 — FastAPI Bridge & Pipeline Integration (`tests/test_api.py`, `tests/test_pipeline.py`, `tests/test_main.py`)**:
   - Exercises `create_app()` HTTP endpoints through `fastapi.testclient.TestClient` backed by an initialized SQLite database, real `ScanPipeline`, and `ConfigManager`.
   - Prior Art: [`tests/test_pipeline.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_pipeline.py) and [`tests/test_main.py`](file:///Users/Jamie/Documents/scripts/python/job-opportunity-scanner/tests/test_main.py).
4. **Seam 4 — SvelteKit 5 Pure TS Domain Engines (`frontend/src/lib/__tests__/`)**:
   - Exercises `serializeSerpQuery()` / `parseSerpQuery()`, `validateSearchProfile()`, and `applyIndustryPack()` via Vitest.

---

## 5. Edge Cases, Safety Nets & Rollback Strategy

- **Preserving `SOL-001` Digest Retry (`needs_alert`) Invariants**:
  - When a user triggers a scan from the UI (`POST /api/scan/trigger`), `send_notifications` defaults to `False`. `ScanPipeline` persists new/changed jobs and populates their Match Explainability columns, but writes nothing to `alerts_sent`. Those jobs immediately appear in the Triage Feed with `Alert Status = pending_alert`, and on the next scheduled daemon scan (`send_notifications=True`), `CandidateMatch.is_retry` (`SOL-001`) automatically includes them in the consolidated SMTP digest.
- **Preserving `user_status` Across ATS Edits**:
  - `JobRepository.upsert()` explicitly inspects any pre-existing `JobModel` row for `job.job_key` and copies `existing.user_status` before merging, guaranteeing that `'saved'` and `'applied'` states are never reset when a company edits a job description (`content_hash` change).
- **Atomic Config & Registry Writes**:
  - Both `ConfigManager.update_config()` and `CompanyRegistry.save_companies()` validate candidate data before touching disk and write to a `.tmp` sibling file followed by `os.replace()`. A failed validation or interrupted write never corrupts `config.yaml` or `data/registries/*.json`.
- **Credential & URL Sanitization**:
  - All adapter error strings persisted to `sources.error_message` or returned by `/api/sources` and `/api/sources/test-query` are sanitized via `BaseAdapter._sanitize_url()`, and environment secrets (`SERPAPI_API_KEY`, `SMTP_PASS`) are exposed exclusively as boolean readiness flags (`env_status`).
