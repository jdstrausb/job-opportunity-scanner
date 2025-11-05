## Step 9 — Logging & Observability Technical Design

### Goals & Scope
- Provide end-to-end visibility into scheduler, pipeline, and support services so operators can diagnose issues using logs alone.
- Standardise structured logging (JSON or key-value) with consistent fields, event taxonomy, and context propagation.
- Capture pipeline metrics (counts, durations, error summaries) and key error scenarios (adapter failures, notification retries, DB issues) in logs without leaking sensitive payloads.

### Requirements & Acceptance Criteria Alignment
- Structured logs emitted to stdout for: scan start/finish, per-source execution, counts per stage, adapter errors, email outcomes, database lifecycle (initialisation, commits, rollbacks).
- Log levels reflect severity: `INFO` for nominal lifecycle milestones, `WARNING` for transient or recoverable issues, `ERROR` for failures requiring attention.
- Support both JSON and human-readable key-value formats selectable via configuration (`AppConfig.logging.format`) with consistent field names in either mode.

### Assumptions
- Python stdlib `logging` remains the primary logging facility; no additional frameworks (e.g., structlog) are introduced in v1.0.
- Log consumers read from container stdout and rely on stable field names for parsing; timestamps must be UTC ISO-8601.
- Observability scope is limited to logging; metrics/alerting integrations are deferred.

### Integration Points
- `app/logging/config.py`: central place to configure handlers, formatters, filters.
- `app/logging/__init__.py` (new helpers) and `app/logging/context.py` (new module) will provide context propagation utilities.
- `app/main.py`: bootstrap sequence that sets log level/format and logs service lifecycle.
- `app/pipeline/runner.py`: main orchestration needing run-level context and per-source summaries.
- `app/scheduler/service.py`: scheduler lifecycle logs (start, next run, shutdown).
- `app/adapters/*`: HTTP fetch diagnostics and adapter error reporting.
- `app/normalization/service.py`, `app/matching/engine.py`, `app/notifications/service.py`: stage-specific instrumentation.
- `app/persistence/database.py`, `app/persistence/repositories.py`: DB connection lifecycle and transactional outcomes.
- Tests under `tests/` requiring new coverage for logging helpers and high-level behaviour.

### Logging Architecture Overview
- **Root Configuration**: `configure_logging` sets level, format, handlers, and installs filters to enrich every record with service metadata and active context (run/source/job IDs).
- **Format Strategy**: JSON formatter produces single-line structured objects with stable field names; key-value formatter mirrors the same fields in `key=value` order for local debugging.
- **Context Propagation**: Context variables (via `contextvars.ContextVar`) carry `run_id`, `source_id`, `job_key`, and arbitrary scoped fields that are automatically merged into log records.
- **Event Taxonomy**: Every log uses an `event` field (e.g., `pipeline.run.started`, `adapter.fetch.error`, `notification.send.success`) to simplify filtering. Components also include a `component` field (e.g., `pipeline`, `adapter.greenhouse`).
- **Redaction Policy**: Never log full job descriptions, SMTP secrets, or raw adapter payloads. Content hashes, counts, identifiers, and truncated excerpts (`…`) are acceptable.

### Structured Log Schema
- **Mandatory fields** (available in both formats):
  - `timestamp`: ISO-8601 UTC (`%Y-%m-%dT%H:%M:%S.%fZ`).
  - `level`: `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`.
  - `event`: Namespaced string describing action/outcome.
  - `component`: Logical subsystem (`cli`, `scheduler`, `pipeline`, `adapter.lever`, etc.).
  - `message`: Human-friendly summary.
- **Context fields**:
  - `run_id`: UUID-like hex identifying a pipeline run (set once per `ScanPipeline.run_once`).
  - `source_id`: Source identifier (`SourceConfig.identifier`) when processing a specific source.
  - `job_key`: Normalised job identifier when dealing with a single job.
  - `notification_id`: Derived from job key + version hash when emitting notification outcomes.
  - `duration_ms`, `count_*`, `error_type`, etc., as needed per event.
- **Static fields**:
  - `service`: Always `job-opportunity-scanner`.
  - `environment`: `production`, `staging`, or `local` (default `local` when unset).

### Context Propagation Plan
- Implement `app/logging/context.py` with:
  - `LogContextVar = ContextVar("log_context", default={})`.
  - Helpers `push_log_context(**kwargs)` returning a token and `pop_log_context(token)` to restore previous state.
  - Context manager `log_context(**kwargs)` to scope context updates for a `with` block.
- Update `configure_logging` to install a `ContextualFilter` that merges `LogContextVar` contents into each record before formatting.
- Provide `get_logger(name: str, component: str)` in `app/logging/__init__.py` that returns a `logging.LoggerAdapter` injecting `component` and default extras.

### Instrumentation Plan by Component
- **Bootstrap (`app/main.py`)**
  - Emit `event="service.starting"` with CLI args summary, resolved config path, and effective log level.
  - After environment and config load, log `event="config.loaded"` with source counts, scan interval, and log format.
  - Surround scheduler/manual branches with `log_context(run_id=None)` to propagate service-level metadata.
  - Log `event="service.stopping"` on graceful shutdown with uptime seconds.

- **Scheduler (`app/scheduler/service.py`)**
  - Log `event="scheduler.started"` when the APScheduler job is registered, including `interval_seconds` and next run timestamp.
  - On shutdown, emit `event="scheduler.stopping"` plus whether waiting for jobs.
  - For manual triggers (`trigger_now`), log `event="scheduler.trigger_now"` with caller indication.

- **Pipeline (`app/pipeline/runner.py`)**
  - Generate `run_id = uuid4().hex` at the start of `run_once` and wrap the entire execution in `log_context(run_id=run_id)`.
  - Emit `pipeline.run.started` (info) and `pipeline.run.skipped` when lock contested, including `lock_held_by`.
  - Before iterating sources, record `pipeline.sources.enumerated` with enabled/disabled counts.
  - For each source, use `with log_context(source_id=source_config.identifier, source_name=source_config.name, ats_type=source_config.type):`
    - Emit `source.run.started`.
    - On success, emit `source.run.completed` with counts (`fetched`, `normalized`, `upserted`, `matched`, `notified`, `errors`, `duration_ms`).
    - On error paths, ensure `source.run.failed` or `source.run.partial_failure` includes `error_type`, `error_message`, and `had_notifications`.
  - After finishing, log `pipeline.run.completed` summarising totals and `alerts_sent`.

- **Adapters (`app/adapters/base.py` and specific adapters)**
  - Ensure HTTP calls log `adapter.fetch.request` (debug) with redacted URL query params, timeout, and method.
  - On success, emit `adapter.fetch.succeeded` with count of jobs returned (post truncation).
  - On recoverable errors (HTTP 5xx, timeouts), log `adapter.fetch.retryable_error` at warning level with `retry_after_seconds=None`.
  - On fatal errors, log `adapter.fetch.error` at error level with `error_type`, `status_code`, and `response_sample` (truncated).

- **Normalization & Matching**
  - `app/normalization/service.py`: replace free-form warnings with structured `normalization.job.missing_description`, include `job_key`, `source_id`.
  - Emit `normalization.job.normalized` (info) once per job with booleans `is_new`, `content_changed`.
  - `app/matching/engine.py`: log `matching.job.matched` (info) or `matching.job.rejected` (debug) with reason arrays; when rejected due to exclusions, include `matched_exclude_terms`.

- **Notifications (`app/notifications/service.py`)**
  - Use `with log_context(job_key=job.job_key, notification_id=f"{job.job_key}:{version_hash[:8]}"`):
    - Log `notification.skip`, `notification.duplicate`, or `notification.send.attempt`.
    - Emit `notification.send.success` with attempt count and recipients.
    - Emit `notification.send.failure` with `attempt`, `error_type`, `retry_remaining`.
  - Summaries use `notification.batch.completed` with counts of sent/skipped/duplicates/failed.

- **Persistence (`app/persistence/database.py`, `app/persistence/repositories.py`)**
  - Log `database.initialising` and `database.initialised` with redacted URL.
  - When sessions are committed or rolled back in `get_session`, use `database.session.committed` / `database.session.rolled_back`.
  - Repository methods emit `repository.job.upserted`, `repository.job.updated_last_seen`, `repository.alert.recorded`, etc., at debug/info with affected row counts; include `duration_ms` if measurable.

- **Utilities / Error Handling**
  - Capture unexpected exceptions with `event="unhandled.exception"` (critical) that includes exception type and context fields.
  - Ensure `ConfigurationError` surfaces log `config.error` before raising to CLI.

### Implementation Guide (Step-by-Step)
1. **Extend Logging Infrastructure**
   - Create `app/logging/context.py` with context variable helpers and context manager.
   - Update `app/logging/config.py` to:
     - Accept optional `environment` parameter (default `local`).
     - Attach `ContextualFilter` combining static metadata (service, environment) and active context.
     - Simplify JSON formatter to serialise `extra` dict automatically (no manual string interpolation).
   - Add `app/logging/__init__.py` helpers (e.g., `get_logger`) that wrap `logging.getLogger` with default `component`.

2. **Bootstrap Enhancements**
   - In `app/main.py`, determine environment label (from env var or default) and call `configure_logging(..., environment=env_label)`.
   - Use `log_context(component="cli")` around startup logs; emit start/stop events as described.
   - Ensure fatal exception paths log `service.startup.failed`.

3. **Scheduler Instrumentation**
   - Inject `component="scheduler"` via logger helper.
   - Add structured logs for start, trigger, shutdown using new event schema.

4. **Pipeline Run Context**
   - Generate `run_id` at the top of `ScanPipeline.run_once`, wrap execution in `log_context(component="pipeline", run_id=run_id)`.
   - Add per-source `with log_context` blocks and structured logs for start/completion/failure.
   - Ensure skipped runs emit distinct event with reason.

5. **Stage Instrumentation**
   - Update adapters, normalization, matching, notification, and repository modules to use `get_logger` with specific `component` identifiers.
   - Replace string interpolation logs with structured `logger.info("...", extra=...)` using the new schema (`event`, `message`, counts).
   - Audit all logging statements to guarantee they include `event` and relevant IDs; avoid duplicative logs.

6. **Context Utilities Adoption**
   - Wrap per-job processing loops with `log_context(job_key=...)`.
   - Ensure context is popped correctly even when exceptions occur (via context manager usage).

7. **Testing & Verification**
   - Add unit tests for `app/logging/context.py` covering nested contexts and restoration.
   - Add formatter tests ensuring JSON output contains all static/context fields and no duplicates.
   - Extend pipeline and notification integration tests to assert that representative events are logged (use `caplog` fixture) with expected fields.
   - Update documentation (README) with instructions on switching log formats and sample output.

8. **Operational Validation**
   - Run `python -m app.main --manual-run --log-level DEBUG` against fixtures to visually inspect log structure.
   - Capture sample JSON log lines for PR review and docs.

### Testing Strategy
- **Unit Tests**
  - `tests/test_logging_context.py`: validate push/pop behaviour and context inheritance.
  - `tests/test_logging_config.py`: ensure both formats produce required fields (`event`, `component`, `service`, `run_id` when context set).
  - `tests/test_pipeline_logging.py`: simulate pipeline run with stubbed dependencies, assert start/completed events recorded with counts.
  - `tests/test_notification_logging.py`: verify skip/duplicate/send paths log correct events with context.
- **Integration Tests**
  - Extend existing integration test(s) to assert high-level events (e.g., `pipeline.run.completed`) appear during a full run using `caplog`.
  - Smoke test ensures adapter retry logs surfaces on simulated HTTP 5xx.

### Operational Considerations
- Document log field dictionary in README for operators.
- Provide guidance on switching JSON vs key-value format via config and overriding at runtime.
- Ensure Docker image redirects stdout to host log collector; no additional file handlers are configured.
- Future work: integrate metrics/emission (Prometheus) or structured log shipping; out of scope for Step 9.

