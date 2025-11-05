## Step 7 — Notification Service Technical Design

### Objective
- Deliver matched job alerts via email using SMTP with configurable templates and robust retry handling.
- Prevent duplicate notifications per job content version by coordinating with persistence and matching layers.
- Provide a clear implementation recipe that can be executed without referencing the broader PRD.

### Scope & Assumptions
- **In scope**: SMTP transport, message templating (HTML + plain text), integration with `AlertRepository`, logging, retry/backoff, failure handling, unit/integration tests with mocked SMTP.
- **Out of scope**: Non-email channels (Slack/SMS), batch digests, per-user preferences beyond existing config, real SMTP connectivity in tests.
- Runtime is Python ≥3.11. Standard library `email`/`smtplib` plus one new dependency (`jinja2>=3.1.0`) for templating will be permitted.
- Environment variables for SMTP come from `app.config.environment.EnvironmentConfig`; app-level email tuning comes from `app.config.models.EmailConfig`.

### Key Integration Points
- `app.matching.utils.build_notification_payload` supplies formatted job/match details for template rendering.
- `app.persistence.repositories.AlertRepository` tracks prior alerts; used to enforce “one alert per job version.”
- `app.config.environment.load_environment_config` and `EnvironmentConfig` provide SMTP connection data and recipient list.
- `app.config.models.AppConfig.email` exposes retry/tls settings (fields: `use_tls`, `max_retries`, `retry_backoff_multiplier`, `retry_initial_delay`).
- `app.utils.highlighting` helpers already produce highlighted snippets for emails.
- Scheduler/pipeline (to be completed in Step 8) will call the notification service with `CandidateMatch` results from `app.matching`.

### Component Architecture
- **NotificationPayloadResolver** (new module `app/notifications/payloads.py`):
  - Accepts `CandidateMatch` → returns enriched payload dict (merges `build_notification_payload` output with metadata such as `job_key`, `version_hash`, `match_quality`, timestamps, retry counters).
  - Ensures consistent schema for template rendering and logging.
- **TemplateRenderer** (new module `app/notifications/templates.py`):
  - Wraps Jinja2 environment with package loader (`app.notifications.templates` directory).
  - Provides `render(subject_template, html_template, text_template, context)` returning subject string and body variants.
  - Caches compiled templates for reuse and exposes exception types for missing variables.
- **SMTPClient** (new module `app/notifications/smtp_client.py`):
  - Thin wrapper around `smtplib.SMTP` (STARTTLS) and optional login.
  - Exposes `send(message: EmailMessage, config: EnvironmentConfig, use_tls: bool)`; handles connection lifecycle, TLS upgrade, authentication, and ensures sockets closed on failure.
  - Designed for easy mocking in tests (injectable class or context manager).
- **NotificationService** (new module `app/notifications/service.py`):
  - Public method `send_candidate_match(candidate: CandidateMatch, env: EnvironmentConfig, app_email_cfg: EmailConfig, alert_repo: AlertRepository) -> NotificationResult`.
  - Flow:
    1. Verify `candidate.should_notify` and `candidate.content_changed`; abort early if false.
    2. Check `alert_repo.has_been_sent(job_key, job.content_hash)`; skip if already alerted.
    3. Build payload via `NotificationPayloadResolver`.
    4. Render subject/body via `TemplateRenderer`.
    5. Construct multipart email (`email.message.EmailMessage`) with both text/plain and text/html.
    6. Dispatch through `SMTPClient` with retry/backoff loop derived from `EmailConfig` (max attempts = `max_retries + 1`).
    7. On success, record via `alert_repo.record_alert(job_key, version_hash, utc_now())` inside the caller’s transaction boundary.
    8. Emit structured logs for success/failure with attempt counts.
- **NotificationResult** (new dataclass in `app/notifications/models.py`):
  - Fields: `job_key`, `version_hash`, `attempts`, `status` (`"sent"`, `"skipped"`, `"duplicate"`, `"failed"`), `error` (optional), `should_persist_alert` flag.
  - Returned to pipeline so Step 8 can collect metrics and decide on persistence commits.

### Template Strategy
- Create package directory `app/notifications/email_templates/` with:
  - `job_alert_subject.j2`
  - `job_alert_body.html.j2`
  - `job_alert_body.txt.j2`
- Context keys (all required):
  - `title`, `company`, `location`, `url`, `posted_at`, `updated_at`, `summary`, `snippets`, `snippets_highlighted`, `match_quality`, `search_terms` (flattened list), `match_reason` (alias of summary), `first_seen_at`, `last_seen_at`, `source_type`, `source_identifier`.
- Subject template example structure (no code in repo): `"New match: {{ title }} at {{ company }}"`.
- HTML template should include sections for key job details, highlighted snippets (<b> tags already applied), and a footer showing “Matched because” bullet list.
- Plain text template mirrors HTML data for mail clients without HTML support.
- TemplateRenderer loads default templates but accepts overrides via optional paths (future-proofing for customization).

### SMTP Delivery & Retry Flow
1. Build `EmailMessage` with From/To derived from environment (`SMTP_SENDER_NAME <SMTP_USER or host>` fallback) and comma-split recipients from `alert_to_email`.
2. Attempt send loop:
   - Attempt counter starts at 1.
   - For attempt N>1, sleep `retry_initial_delay * (retry_backoff_multiplier ** (N-2))` seconds; clamp to sane upper bound (e.g., 60s) to avoid runaway delays.
   - Wrap each attempt in try/except catching `smtplib.SMTPException`, socket errors, or template failures (the latter should not retry).
   - Log at WARNING for transient retry attempts; ERROR after final failure with stack trace.
3. On success, break loop; return NotificationResult with `status="sent"` and attempts count.
4. On exhaustion, return `status="failed"` along with error chain; caller must avoid recording alert in DB.

### Alert Deduplication Logic
- Dedup key: `(job_key, version_hash)` where `version_hash = job.content_hash`.
- NotificationService checks `AlertRepository.has_been_sent` before rendering. If true, return NotificationResult `status="duplicate"` without sending.
- After successful send, call `record_alert` to persist (caller commits).
- Provide helper method `should_record_alert(result)` to centralize logic (record only when `status=="sent"`).

### Logging & Metrics
- Use structured logger (`logging.getLogger(__name__)`):
  - `INFO` on successful send (fields: job_key, company, attempts, recipients).
  - `WARNING` for retry attempt with attempt number and exception summary.
  - `ERROR` for final failure, include exception stack, job metadata, retry info.
- Expose counters for pipeline metrics (Step 8): e.g., number sent, skipped, duplicates, failures per run.

### Error Handling & Resilience
- Template rendering errors: treat as fatal (no retry) because developer misconfiguration; raise `NotificationTemplateError`.
- Recipient parsing errors: fail fast with clear message referencing `ALERT_TO_EMAIL` env var.
- SMTP auth missing when required: emit `ConfigurationError` to bubble up to startup checks.
- Always close SMTP connection (use context manager or `try/finally`).

### Security Considerations
- Never log SMTP password or full auth payload.
- Mask recipient addresses in logs if multiple (log length or hashed values) if needed; otherwise include domain only.
- Validate TLS: call `starttls(context=ssl.create_default_context())` when `use_tls` true; allow configurable port for 465 (implicit TLS) by detecting `SMTP_PORT`.

### Step-by-Step Implementation Guide
1. **Dependencies & Package Layout**
   - Add `jinja2>=3.1.0` to `pyproject.toml` dependencies.
   - Create package structure under `app/notifications/`:
     - `models.py`, `payloads.py`, `templates.py`, `smtp_client.py`, `service.py`, `email_templates/`.
   - Update `app/notifications/__init__.py` to export new classes/functions.
2. **Define Models & Exceptions**
   - In `models.py` declare `NotificationResult` dataclass and custom exceptions (`NotificationError`, `NotificationTemplateError`, `SMTPDeliveryError`).
   - Include helper predicates (`is_success`, `should_record_alert`).
3. **Build TemplateRenderer**
   - Instantiate Jinja2 `Environment` with `PackageLoader("app.notifications", "email_templates")`.
   - Enable autoescape for HTML templates; configure strict undefined to catch missing fields.
   - Provide `render(context)` method returning dict with `subject`, `html_body`, `text_body`.
4. **Author Email Templates**
   - Populate subject/HTML/text `.j2` files with placeholders listed above.
   - HTML should use semantic sections (`<h1>`, `<ul>`), highlight matched keywords using pre-highlighted snippets, and include fallback text for missing data.
   - Text template uses ASCII-friendly formatting and indentation.
5. **Implement NotificationPayloadResolver**
   - Function `build_notification_context(candidate: CandidateMatch) -> dict` merges:
     - Job metadata from `candidate.job`.
     - `build_notification_payload` output.
     - Additional fields: `version_hash=candidate.job.content_hash`, `search_terms=payload["matched_terms_flat"]`, `first_seen_at`/`last_seen_at` isoformat, `source_type`, `source_identifier`, `match_quality`.
   - Include defensive defaults (e.g., `location="Remote"` already provided).
6. **Create SMTPClient Wrapper**
   - Class handles connection, TLS, login, sendmail.
   - Accept optional injectable `smtp_factory` (default `smtplib.SMTP`) to simplify mocking.
   - Normalize recipients by splitting comma-separated emails and validating with `_is_valid_email`.
7. **Implement NotificationService**
   - Constructor accepts `template_renderer`, `smtp_client`, `alert_repo`, logger.
   - Method `send_candidate_match` executes flow described above.
   - Expose helper for batch processing (future Step 8) e.g., `send_notifications(matches: Iterable[CandidateMatch], env, email_cfg) -> List[NotificationResult]`.
   - Ensure method operates within an existing DB session/transaction handed in by caller; `alert_repo` already tied to session.
8. **Wire Up Module Exports**
   - Update `app/notifications/__init__.py` to expose `NotificationService`, `NotificationResult`, `TemplateRenderer`, `SMTPClient`.
   - Document module responsibilities inline.
9. **Pipeline Integration Hooks**
   - Define interface expectation for Step 8: pipeline obtains `NotificationService` instance during bootstrap (pass repositories + configs).
   - Document that pipeline must call `alert_repo.record_alert` only when `NotificationResult.should_record_alert()` is true and commit session afterwards.
10. **Testing**
    - See testing section below; implement fixtures and mocks accordingly.
11. **Documentation**
    - Update README “Features” to mention templated email notifications and retry logic.
    - Optionally add section to existing overall tech spec referencing this detailed doc.

### Testing Strategy
- **Unit Tests**
  - `tests/test_notifications_payloads.py`: verify context builder populates required keys, handles missing optional fields, and correctly maps match payload data.
  - `tests/test_notifications_templates.py`: use `TemplateRenderer` with sample context to ensure subject/body contain expected strings and HTML escapes.
  - `tests/test_notifications_smtp.py`: mock `smtplib.SMTP` to confirm TLS handshake, login, and `send_message` call; test authentication optionality and recipient parsing.
  - `tests/test_notifications_service.py`: patch SMTP client to raise on first send then succeed; assert retry/backoff logic and AlertRepository interactions (use `pytest-mock` for verifying `record_alert` called once).
  - Validate duplicate detection path returns `"duplicate"` without calling SMTP.
- **Integration Tests**
  - Extend `tests/integration/test_normalization_matching.py` or add new `tests/integration/test_notifications.py`:
    - Use in-memory SQLite + mocked SMTP to assert full flow: normalization → matching → notification service (simulate pipeline).
    - Ensure alert record inserted after successful send and omitted on failure.
    - Cover scenario where two matches share same `content_hash` to confirm dedup.
- **Fixtures & Utilities**
  - Provide reusable mock SMTP class capturing sent messages for assertions.
  - Add template context fixture based on existing `job_matching` fixture.
- **Coverage Targets**
  - Aim for >90% branch coverage on `app/notifications` modules due to critical reliability role.

### Observability & Metrics Hook
- Expose optional callback or event emitter on NotificationService (`on_result`) so Step 8 can aggregate metrics (counts per status).
- Log structured payload identifiers (job_key, company, match_quality) to correlate with matching logs.
- Consider future enhancement: push counters to Prometheus/exporter; document placeholder functions.

### Risks & Mitigations
- **SMTP provider variance**: Document port/TLS combinations tested (587 STARTTLS, 465 implicit TLS). Allow override in config and ensure tests cover both paths using mocks.
- **Template drift**: Enforce strict Jinja undefined to surface missing keys during tests. Provide comprehensive unit tests around context building.
- **Backoff tuning**: Default backoff may delay alerts; allow configuration via `EmailConfig` and note recommended defaults in README.
- **Multiple recipients**: Clarify in docs that `ALERT_TO_EMAIL` may contain comma-separated values; ensure deduped/trimmed list before send.

### Open Questions / Follow-Up
- Should we support attachment of CSV summaries in future versions? (Out of scope; note for backlog.)
- Will Step 8 batch multiple job matches into a single email? Current design assumes per-job messages; revisit if batching becomes requirement.
- Confirm whether HTML emails must match specific branding; currently using neutral styling.
