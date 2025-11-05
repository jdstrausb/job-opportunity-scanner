## Step 8 — Scheduler & Pipeline Technical Design

### Objective & Scope
- Deliver the execution layer that ties together configuration, adapters, normalization, matching, persistence, and notifications.
- Integrate a scheduler that triggers scans at the configured interval without overlapping runs.
- Provide a CLI entry point (`python -m app.main` or the `job-scanner` console script) that supports both daemon mode and a manual one-shot run.

### Requirements & Acceptance Criteria Alignment
- Polling cadence: trigger the pipeline on the `scan_interval` provided in `config.yaml`; default remains 15 minutes when unspecified.
- Reliability: one failing source must not abort the full run; errors are logged and persisted in `sources` health metadata.
- Notification handoff: pipeline must obtain a `NotificationService` instance during bootstrap, pass `CandidateMatch` objects to `send_candidate_match` / `send_notifications`, and commit alert records when `NotificationResult.should_record_alert()` is `True`.
- Persistence: ensure job upserts, last_seen updates, and alert history writes are committed before the next run begins; SQLite WAL mode is already configured.
- Manual run: `--manual-run` executes a single scan synchronously and exits with a 0/1 status code.

### High-Level Architecture
- **Pipeline Runner** (`app/pipeline`): Orchestrates a single scan, returning structured metrics and raising exceptions only for fatal conditions (e.g., configuration errors).
- **Scheduler Service** (`app/scheduler`): Wraps APScheduler to trigger the pipeline runner with `max_instances=1` and `coalesce=True`, guarding against overlapping runs.
- **CLI Bootstrap** (`app/main.py`): Loads configuration & environment, configures logging, initializes persistence, instantiates shared services, and either performs a manual run or starts the scheduler.
- **Shared Services**: `NotificationService`, `KeywordMatcher`, and adapter factory are created once during bootstrap and injected where needed.

### Module Responsibilities
#### `app/pipeline/__init__.py` and `app/pipeline/runner.py`
- Define a `ScanPipeline` (or `PipelineRunner`) class initialized with `AppConfig`, `EnvironmentConfig`, shared service instances, and optional hooks for testing.
- Provide `run_once()` that executes the full scan and returns a `PipelineRunResult` dataclass (counts, durations, alerts sent, per-source summaries).
- Maintain a `threading.Lock` (or similar guard) to prevent overlapping executions even if APScheduler misfires.

#### `app/pipeline/models.py`
- Dataclasses for:
  - `SourceRunStats` (source identifier, fetched count, normalized, upserted, matched, notified, errors).
  - `PipelineRunResult` (start/end timestamps, total duration, aggregate counts, list of `SourceRunStats`, flags such as `had_errors`, `alerts_sent`).

#### `app/scheduler/service.py`
- `SchedulerService` class wrapping `apscheduler.schedulers.background.BackgroundScheduler`.
- Configure defaults: `job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": scan_interval_seconds}`.
- Expose `start()`, `shutdown()`, `trigger_now()` (for manual testing), and `is_running`.
- Accept the pipeline callable plus interval seconds and optional shutdown event.

#### `app/main.py`
- Parse CLI arguments (`--config`, `--manual-run`, `--log-level`).
- Call new helper functions:
  - `load_runtime_config(path, log_level_override)` that returns `(app_config, env_config)` with `scan_interval_seconds` set via `parse_duration` and `validate_duration_range`.
  - `configure_logging(level, format)` (implemented in `app/logging/config.py`) before running anything else.
- Initialize database (`init_database(env_config.database_url)`), instantiate shared services, and wire scheduler vs manual run.

### Execution Flow
#### Bootstrap Sequence
1. Parse CLI args.
2. Configure logging early (honoring `--log-level` and `AppConfig.logging`).
3. Load app & environment configuration, populate `AppConfig.scan_interval_seconds`.
4. Initialize database engine, ensuring parent directory exists (already handled), and run migrations on startup.
5. Instantiate service singletons:
   - `NotificationService()` (reuse across runs).
   - `KeywordMatcher(app_config.search_criteria)`.
   - Adapter factory access via `get_adapter`.
6. Build `ScanPipeline` with the above dependencies.

#### Per-Run Workflow (`ScanPipeline.run_once`)
1. Capture `run_started_at = utc_now()` and reset per-run metrics.
2. Acquire the run lock; if already locked, log a warning and return a skipped result (scheduler coalescing should minimize this).
3. For each `SourceConfig` in `app_config.sources` in defined order:
   - Skip if `enabled` is `False`.
   - Within `with get_session() as session`:
     1. Instantiate repositories: `JobRepository`, `AlertRepository`, `SourceRepository`.
     2. Instantiate `JobNormalizer(job_repo, scan_timestamp=run_started_at)` so all jobs in this run share the same timestamp.
     3. Fetch jobs via adapter (`get_adapter(source_config, app_config.advanced)`), handling `AdapterError` subclasses:
        - On success, update `SourceRepository.update_success`.
        - On error, call `SourceRepository.update_error` with message, add entry to stats, continue to next source.
     4. Normalize each `RawJob`:
        - Accumulate `NormalizationResult`s.
        - If `result.should_upsert` → `job_repo.upsert(result.job)`.
        - Else (unchanged) → `job_repo.update_last_seen(job_key, run_started_at)`.
     5. For each normalized job where `result.should_re_match` is `True`, evaluate `KeywordMatcher` and wrap in `CandidateMatch`; for unchanged jobs set `should_notify=False` without calling the matcher.
     6. Collect `CandidateMatch` objects that have `candidate.should_notify` to send notifications.
     7. Call `NotificationService.send_notifications(matches_to_notify, env_config, app_config.email, alert_repo)` (pass iterable; service already handles empty iterables).
     8. Determine whether to commit immediately:
        - Any `NotificationResult.should_record_alert()` → keep session open until `send_notifications` completes, then `session.commit()` explicitly (safe even inside context manager) so alert records persist.
        - If no alerts were sent but there were job upserts/updates, `session.commit()` still occurs via the context manager exit.
     9. Update source stats with counts: number fetched, normalized, persisted, matched, sent, failures.
4. After processing all sources, release the lock and compute `run_finished_at`, total duration, aggregated metrics, and whether any errors occurred (source fetch errors or notification failures).
5. Log a structured summary event with aggregated counts and duration; return the `PipelineRunResult`.

### Notification Integration Details
- Pipeline must construct `AlertRepository` from the same session used during normalization so that `NotificationService` can deduplicate against freshly persisted data.
- `CandidateMatch` objects already set `should_notify` based on match result; pipeline should filter using this property before calling the service.
- Capture and log `NotificationResult` statuses, increment per-source counts for `sent`, `skipped`, `duplicate`, `failed`.
- If any `send_notifications` call raises an unexpected exception, catch it, log at error level, mark the source run as failed, and continue to the next source.

### Transactions & Session Management
- One SQLAlchemy session per source per run keeps transactions short and localized.
- `with get_session()` automatically commits on successful exit; explicit commits after notifications are acceptable and harmonize with the context manager.
- Ensure `session.rollback()` is triggered by exceptions (already handled by the context manager), but augment source stats and logging so failures are visible in run summaries.

### Scheduling Behaviour
- Use `BackgroundScheduler` from APScheduler running in the main thread; `.start()` will spawn worker threads but return immediately so the CLI can block on a `threading.Event`.
- Job registration:
  - Trigger type: `IntervalTrigger(seconds=app_config.scan_interval_seconds, timezone=timezone.utc)`.
  - Pass the pipeline callable via `add_job(pipeline.run_once, id="job-scan", replace_existing=True)`.
  - Provide `next_run_time=datetime.now(timezone.utc)` so the first run starts immediately after startup.
- Add signal handlers (SIGINT, SIGTERM) in `app/main.py` to call `scheduler.shutdown(wait=False)` and close the database (`close_database()`).
- Expose a force-run method (e.g., CLI option `--run-now` if desired later) by invoking `scheduler_service.trigger_now()` which calls `pipeline.run_once()` synchronously while holding the lock.

### Logging & Observability
- Implement `configure_logging(level: str, format: str)` in `app/logging/__init__.py` (or a new `config.py`) to set key-value or JSON formatting as determined by `AppConfig.logging.format`.
- Emit log events for:
  - Scheduler start/stop and next run time.
  - Run start/finish with duration.
  - Source fetch start/end (counts, duration).
  - Adapter errors (status code, URL).
  - Normalization results (new vs updated counts).
  - Notification summary (sent/skipped/duplicates/failed).
- Ensure secrets (SMTP credentials) never appear in logs; redact or omit entire config objects.

### Configuration Handling
- After loading `AppConfig`, compute `scan_interval_seconds = parse_duration(app_config.scan_interval)` and validate range via `validate_duration_range`.
- Allow `EnvironmentConfig.log_level` to override `AppConfig.logging.level` unless the CLI flag is passed (priority: CLI > env > config).
- Document default database path `/data/job_scanner.db`, but allow overrides via `DATABASE_URL`.

### Error Handling & Resilience
- Wrap each source processing block in `try/except AdapterError` and generic `Exception` to prevent a single failure from halting the run.
- If adapter fetch partially succeeds (returns empty because of transient errors), treat as non-fatal but mark the source stats with `had_errors=True`.
- If pipeline-wide fatal errors occur (e.g., configuration invalid), raise to caller so CLI exits with `sys.exit(1)`.
- Ensure scheduler job rethrows exceptions to APScheduler; they will be logged but job remains scheduled due to `max_instances=1`.

### CLI Behavior
- `python -m app.main` or `job-scanner` (entry point already declared in `pyproject.toml`) executes the same logic.
- `--manual-run`: skip scheduler setup, call `pipeline.run_once()` once, print/log summary, return exit code 0 on success, non-zero if `had_errors` or an exception bubbles up.
- `--log-level`: overrides logging level prior to config load; log the effective level once.
- Standard exit:
  - Daemon mode blocks on `scheduler_service.wait_forever()` (loop on `threading.Event().wait()`) until interrupted.
  - Handle `KeyboardInterrupt` gracefully with a friendly log message and exit code 0.

### Testing Strategy
- **Unit Tests**
  - `tests/test_pipeline.py`: exercise `ScanPipeline` with stub adapters, repositories (using temporary SQLite), and fake notification service to verify counts, commits, and error handling.
  - `tests/test_scheduler.py`: assert `SchedulerService` registers jobs with expected defaults, prevents overlaps, and shuts down cleanly.
  - `tests/test_main.py`: mock configuration loaders and pipeline to ensure CLI switches between manual and scheduled modes correctly and propagates exit codes.
- **Integration Tests**
  - Extend `tests/integration/test_notifications.py` or add `tests/integration/test_pipeline_notifications.py` to cover end-to-end flow (adapter → normalization → matching → notifications) using in-memory SQLite and mocked SMTP; ensure alert deduplication persists between runs.
  - Add scheduler smoke test with accelerated interval (e.g., 1 second) using patched pipeline to ensure `max_instances=1` prevents overlap.
- **Manual QA**
  - Run `python -m app.main --manual-run` with sample config to verify logs and database contents.
  - Start daemon mode, inspect `apscheduler` logs for next run times, stop with Ctrl+C, and ensure database handles multiple runs.

### Implementation Checklist
1. Create `app/logging/config.py` (or enhance `app/logging/__init__.py`) with a `configure_logging` helper that respects level/format settings.
2. Add `app/pipeline/models.py` and `app/pipeline/runner.py` implementing the pipeline orchestration, metrics, and locking.
3. Update `app/pipeline/__init__.py` to export `ScanPipeline` and related dataclasses.
4. Implement `app/scheduler/service.py` with `SchedulerService` and update `app/scheduler/__init__.py` exports.
5. Enhance `app/main.py` to perform full bootstrap, wire dependencies, support manual run, and start the scheduler; ensure `job-scanner` console script works.
6. Ensure configuration loader populates `AppConfig.scan_interval_seconds` and respects CLI/env overrides.
7. Add structured logging calls across the new modules and update any existing modules if necessary to support injected logger instances.
8. Write the unit and integration tests outlined above; update fixtures/mocks as needed.
9. Update documentation where appropriate (e.g., README run instructions if behaviour changes) and regenerate coverage if tooling requires.
10. Verify `uv sync` / `pip install -e .` still succeeds and run `pytest` to confirm green suite.

### Assumptions & Follow-Ups
- Scheduling uses UTC for consistency; local timezone support is out of scope for v1.0.
- APScheduler is acceptable per project dependencies; no alternative scheduler is planned.
- Future Step 9 (Observability) will enrich logging/metrics; current implementation focuses on correctness and resilience.
- Docker entrypoint will continue to call `python -m app.main`; no additional process manager is required.
