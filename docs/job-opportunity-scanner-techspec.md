## Job Opportunity Scanner — Technical Design (v1.0)

### Context and Scope
- Goal: Implement an automated service that polls ATS APIs for configured companies, evaluates postings against keyword rules, deduplicates results, and emails actionable alerts.
- Source PRD: `docs/job-opportunity-scanner-prd.md`.
- Audience: Engineers who will implement the initial production-ready version (v1.0).
- Scope: Core service implementation, configuration, persistence, notification, observability, and containerization.

### Existing System Assessment
- Repository currently contains documentation only; there is no existing application code, database schema, or infrastructure setup.
- No constraints detected from prior implementations; greenfield service design assumed.

### Assumptions
- Runtime: Python 3.11 (or later) with access to standard libraries plus approved third-party packages (`requests`, `PyYAML`, `APScheduler`, `SQLAlchemy` or `sqlite3`, `email` utilities).
- Deployment target: Linux-based VPS running Docker (Dockerfile will define image).
- Environment variables available for secret management (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `ALERT_TO_EMAIL`).
- Polling interval defaults to 15 minutes and can be overridden in `config.yaml` (ISO-8601 duration or human-readable string, defined in configuration schema).
- ATS endpoints expose updated timestamps and stable job identifiers; if not, adapter must derive equivalents (document fallback per adapter).
- Email provider supports TLS; assume STARTTLS on standard port unless overridden.

### High-Level Architecture
- **Configuration Loader** reads `config.yaml`, validates schema, and produces in-memory config objects.
- **Scheduler Service** orchestrates periodic execution of the scanning pipeline at the configured interval.
- **Scanning Pipeline** iterates through configured sources, delegating to ATS-specific adapters (Greenhouse, Lever, Ashby) for job retrieval.
- **Normalization Layer** maps raw postings to a unified `Job` domain model.
- **Persistence Layer** (SQLite via SQLAlchemy or raw SQL) stores jobs, sources, and alert history; provides change detection helpers.
- **Matching Engine** applies keyword rules (required terms, OR groups, exclusion list) against job title and description.
- **Notification Service** formats and sends email alerts; records sent notifications to prevent duplicates.
- **Logging & Metrics** emit structured events to stdout for observability.
- **Containerization** packages service and cron-like scheduler into a single Docker image.

### Data Flow Summary
1. Service bootstraps: loads configuration, initializes database, scheduler, logging.
2. On each tick:
   - For each source in configuration, fetch postings via adapter.
   - Normalize jobs and upsert into persistence layer.
   - Determine job state (new or updated) via stored metadata.
   - Evaluate matching rules for new/updated jobs.
   - For passing jobs, send email if no alert sent for current version; record alert.
3. Errors per source are logged and bubbled but do not abort remaining sources.

### Configuration Management
- Single YAML file (`config.yaml`) in project root.
- Schema elements:
  - `sources`: list of objects with `name`, `type`, `identifier`, optional `enabled` flag.
  - `scan_interval`: duration string; validated to minimum threshold (e.g., 5 minutes).
  - `search_criteria`: object containing `required_terms`, `keyword_groups`, `exclude_terms` arrays.
- Implement validation layer with descriptive errors; provide sample file in docs or `config.example.yaml`.
- Config is read at startup; runtime changes require restart.

### Database Design (SQLite)
- **jobs**: `job_key` (PK), `source_type`, `source_identifier`, `external_id`, `title`, `company`, `location`, `url`, `posted_at`, `updated_at`, `first_seen_at`, `last_seen_at`, `content_hash`.
- **sources**: `source_identifier` (PK), `name`, `type`, `last_success_at`, `last_error_at`, `error_message`.
- **alerts_sent**: `job_key` (PK), `version_hash`, `sent_at`.
- Migration strategy: simple SQL migration executed on startup if tables absent; consider Alembic for future versions but optional for v1.0.
- `content_hash` computed from normalized title + description + location to detect meaningful changes.

### Core Modules and Responsibilities
- **app/main.py** (entry point): parse args, load config, bootstrap dependencies, start scheduler.
- **config/**: YAML parsing, schema validation, default handling, typed config objects.
- **adapters/**:
  - `greenhouse.py`: fetch API (jobs board endpoint), paginate if necessary, map to domain.
  - `lever.py`: handle company feed, parse JSON, handle posted/updated timestamps.
  - `ashby.py`: call GraphQL or REST endpoint; document required headers.
  - Common adapter base to enforce interface (`fetch_jobs(source_config) -> list[RawJob]`).
- **normalization/**:
  - Convert `RawJob` to `Job` (title, description, location, url, posted_at, updated_at).
  - Ensure consistent timezone handling (UTC).
- **persistence/**:
  - Database session management (context manager), schema creation, CRUD operations.
  - Functions: `get_job(job_key)`, `upsert_job(job)`, `record_alert(job_key, version_hash)`, `has_alert(job_key, version_hash)`.
- **matching/**:
  - Text normalization (case-folding, punctuation stripping).
  - Search scope: title + description; possibly location for future extension.
  - Evaluate required terms, OR groups, exclusion list; log match rationale.
- **scheduler/**:
  - Use APScheduler or custom loop; ensures sequential run to avoid overlapping execution.
  - Handles graceful shutdown signals.
- **notifications/**:
  - Email template generation (plain text) summarizing match info and matched keywords.
  - SMTP client with TLS; retries limited times (e.g., 3 attempts with exponential backoff).
- **logging/**:
  - Configure structured logging (JSON or key=value) to stdout.
  - Provide helper functions for consistent fields (source, job_key, event).
- **utils/**:
  - Hashing helpers, time utilities, keyword highlighting for emails.

### Scheduler and Execution Model
- Initialize scheduler after configuration validation.
- On each tick, execute pipeline synchronously to maintain order and simplify locking.
- Ensure a lock/flag prevents overlapping runs; log warning if previous run exceeds interval.
- Support manual trigger via CLI flag for testing.

### Error Handling and Resilience
- Adapter failures: catch exceptions, log error with context, continue to next source.
- Database errors: retry transient errors; fail fast if schema corrupt, exit with message.
- Email send failures: log error, do not mark alert as sent; optionally retry next cycle.
- Global exception handler around pipeline to prevent scheduler crash; log stack trace.

### Security Considerations
- Never log secrets; obfuscate SMTP credentials in debug statements.
- Validate email addresses from config; enforce TLS on SMTP (configurable override).
- Limit external HTTP request timeouts and apply user-agent string.

### Deployment and Packaging
- Dockerfile stages:
  - Base: Python slim image.
  - Install dependencies via `uv` using `pyproject.toml`.
  - Copy application source and default config example.
  - Set entrypoint to start service (e.g., `python -m app.main`).
- Provide instructions for mounting volume for SQLite (`/data/job_scanner.db`).
- Document environment variables and sample docker-compose snippet in README (future work).

### Monitoring and Logging
- Structure logs with fields: `timestamp`, `level`, `event`, `source`, `job_key`, `details`.
- Key log events:
  - Scheduler start/stop.
  - Source fetch start/end with counts.
  - Adapter error (include HTTP status, retry behavior).
  - Match decisions with matched keywords for transparency.
  - Email send success/failure.
- Provide optional log level configuration via env var (`LOG_LEVEL`).

### Testing Strategy
- **Unit Tests**
  - Config parser validation (invalid/missing fields).
  - Keyword matching logic across combinations, punctuation, case, exclusion terms.
  - Adapter response parsing using fixtures for each ATS.
  - Persistence functions (upsert, change detection, alert recording).
- **Integration Tests**
  - End-to-end pipeline using mocked adapters returning sample data.
  - Email service test using local SMTP stub (e.g., `smtplib` debug server/mocked client).
  - Scheduler invocation with shortened interval to ensure no overlapping runs.
- **Manual/Smoke Tests**
  - Run container locally with sample config and inspect logs + SQLite contents.
  - Verify email delivered to test inbox.

### Implementation Guide (Step-by-Step)
1. **Project Scaffolding**: Initialize Python package layout (`app/`, module directories), create `pyproject.toml` or `requirements.txt`, configure linting/formatting (optional `black`, `ruff`).
2. **Configuration Module**: Implement YAML loader, schema validation, defaults, and unit tests; supply `config.example.yaml`.
3. **Domain Models**: Define `Job`, `SourceConfig`, and matching rule data structures (dataclasses or similar); include helpers for hashing and timestamps.
4. **Persistence Layer**: Set up SQLite connection handling, create schema migration, implement upsert and query helpers, and associated tests.
5. **ATS Adapters**: Implement Greenhouse, Lever, Ashby adapters using shared base; include mapping and raw-to-normalized transformation tests with recorded fixtures.
6. **Normalization & Matching**: Build normalization utilities and keyword matching engine; ensure match rationale returns matched terms/groups for notifications.
7. **Notification Service**: Implement SMTP email sender, templating, retry logic, and tests using mocked SMTP client.
8. **Scheduler & Pipeline**: Wire components into pipeline execution function; integrate scheduler to run at configured interval; add CLI entry point.
9. **Logging & Observability**: Configure logging format, add log statements across pipeline, ensure structured output.
10. **Dockerization**: Write Dockerfile, confirm image builds, document volume mount for SQLite and environment variable configuration.
11. **End-to-End Validation**: Conduct integration tests, run sample scan using mock data or limited real endpoints, validate alert deduplication and logs.
12. **Documentation Updates**: Update README with setup, configuration, and run instructions; capture known limitations.

### Acceptance Criteria Traceability
- Source configuration (Story 1) → Steps 2, 5, validation tests.
- Keyword rules (Story 2) → Step 6 matching engine implementation.
- Polling & resilience (Story 3) → Steps 5, 8.
- Deduplication & persistence (Story 4) → Step 4 persistence design.
- Alert decision logic (Story 5) → Step 6 + Step 4 (version hash).
- Email notifications (Story 6) → Step 7 + Step 6 rationale integration.
- Observability/reliability (Story 7) → Step 9 logging, Step 8 scheduler error handling.
- Packaging/deployment (Story 8) → Step 10 Dockerization.

### Risks and Mitigations
- **API schema changes**: Mitigate by isolating adapters and adding regression tests with recorded payloads.
- **Rate limiting**: Implement configurable request throttling/backoff per adapter.
- **Email delivery issues**: Provide configuration for SMTP retries and alert logs; document connection troubleshooting.
- **SQLite locking**: Use serialized connection mode or ensure single-writer execution via synchronous pipeline.
- **Long-running scans**: Warn via logs when runtime exceeds interval; allow configuration to skip overlapping runs.

### Open Implementation Questions
- Confirm acceptable third-party libraries (any restrictions on APScheduler, SQLAlchemy?).
- Should we support environment-based overrides for search criteria (e.g., remote only) or keep YAML-only?
- Do we need to redact job descriptions in logs to avoid PII storage?
- How aggressive should retry/backoff be for adapters and SMTP (default counts, intervals)?
- Any requirements for multi-user configuration support in future iterations?
