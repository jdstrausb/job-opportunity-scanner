# End-to-End Validation Technical Specification (Step 11)

## Overview
- Step 11 of the implementation guide validates the entire Job Opportunity Scanner stack — adapters → normalization → persistence → matching → notifications — before shipping.
- This specification defines the testing artifacts, fixtures, and runbooks needed to prove the system meets PRD guarantees for reliability, alert deduplication, and observability without requiring the reader to open the PRD.
- Deliverables: an automated integration test suite, a reproducible sample scan harness (mock-data and optional limited real endpoints), and log/dedup verification steps.

## Objectives & Acceptance Criteria Alignment
- **End-to-End Coverage**: Execute the real `ScanPipeline` with concrete `AppConfig`, `EnvironmentConfig`, adapters, notification service, and SQLite persistence to ensure the workflow behaves identically to production.
- **Alert Deduplication**: Demonstrate “one alert per job version” by running successive scans, confirming that unchanged jobs do not re-trigger notifications while updated content results in one additional alert (PRD Story 4 & 5).
- **Logging & Observability**: Capture structured log events for scan start/finish, per-source stats, adapter errors, and notification outcomes, proving Story 7 acceptance criteria are satisfied.
- **Sample Scan Runbook**: Provide a deterministic manual scan using mock data (no external network needed) plus guidance for an optional limited real run so product can be validated with real careers pages when credentials and network are available.
- **Traceability to PRD**: Tests must assert the behaviours promised in user stories 2–7 (keyword rules, polling resilience, dedup/persistence, alert decisioning, email notifications, logging).

## Assumptions & Dependencies
- Tests run via `pytest` and can rely on fixtures already present under `tests/fixtures`.
- SQLite is the persistence layer; integration tests may create temporary file-backed databases under `tmp_path` to emulate production disk I/O.
- Outbound SMTP is mocked during automated runs; actual credentials are only needed for the optional manual run.
- Network access is restricted in CI; mock adapters and recorded ATS payloads are required for deterministic validation.
- Logging is configured through `app.logging.configure_logging` and emits to stdout; tests can intercept log records via `caplog`.

## Integration Surface Summary
- `app/main.py`: CLI entry point that configures logging, loads configs, initialises the DB, and kicks off the pipeline in manual mode.
- `app/pipeline/runner.ScanPipeline`: orchestrates fetching, normalization, persistence, matching, and notifications. It is the primary subject under test.
- `app/adapters.factory.get_adapter` & adapter implementations: provide raw jobs from Greenhouse/Lever/Ashby; for validation we substitute deterministic fixture-backed adapters.
- `app/normalization.JobNormalizer`, `app.matching.KeywordMatcher`, `app.notifications.NotificationService`: core services exercised by the pipeline.
- `app.persistence.database` & `app.persistence.repositories.{JobRepository, AlertRepository, SourceRepository}`: persistence boundary whose state must be asserted for deduplication guarantees.
- `app.logging` and `app.logging.context`: structured logging utilities whose output is verified.
- `tests/fixtures/ats_responses/*`: existing sample payloads reused for deterministic mock data.

## Validation Strategy

### Automated Integration Tests
Create a dedicated module `tests/integration/test_end_validation.py` (marker `pytest.mark.integration`). Each test spins up a fresh in-memory or temporary-file SQLite database via `init_database`, constructs real services, and patches `get_adapter` plus `SMTPClient.send_email` to keep runs hermetic.

| Test Case | Scenario & Goal | Key Assertions | Components Exercised |
| --- | --- | --- | --- |
| `test_full_pipeline_sample_fixture` | Run `ScanPipeline.run_once()` against two sources whose adapters return fixture jobs (matching + non-matching + excluded). | `PipelineRunResult` totals for fetched/normalized/upserted/matched/notified, `JobRepository.get_all_active()` count, notification send call count, persisted `AlertRepository` rows, log events `pipeline.run.started/completed`. | `ScanPipeline`, fixture adapters, normalization, matching, notification service, persistence, logging. |
| `test_repeated_run_dedupes_alerts` | Execute two back-to-back runs with identical fixture payloads. | First run sends notifications for matches; second run reports zero `total_notified`, no additional `AlertRepository` entries, log contains `notification.skip` reason `duplicate`. | Alert repository, notification service, log capture. |
| `test_updated_job_triggers_single_additional_alert` | Modify one job’s description between runs while keeping others unchanged. | Updated job produces exactly one new alert (one per source) and increments `alerts_sent` appropriately; `JobRepository` reflects updated content hash and `last_seen_at`; logs contain `normalization.job.normalized` with `content_changed=true`. | Normalization change detection, persistence upsert, notification dedup, logging. |
| `test_manual_run_emits_summary_logs` | Invoke `app.main.main()` in `--manual-run` mode with stubbed config/env to ensure CLI wiring produces logs and exit codes. | `configure_logging` called with expected format, `ScanPipeline.run_once` invoked once, `service.manual_scan.completed` log entry emitted with fetched/matched counts, exit code 0/1 matches `PipelineRunResult.had_errors`. | CLI parsing, logging bootstrap, pipeline invocation, log assertions. |

### Sample Scan Harness
- Add a runnable helper (e.g., `scripts/run_sample_scan.py`) that loads a provided YAML config, injects fixture-backed adapters, and executes a manual scan using the real pipeline. This script should:
  - Accept `--fixtures` pointing to a directory of recorded ATS responses.
  - Optionally accept `--database` to run against a temporary SQLite file (default `./data/sample_end_validation.db`).
  - Print a concise summary plus the location of the generated log file so PMs can validate end-to-end behaviour without running pytest.
- When network access is approved, document how to switch the same script to “limited real endpoint mode” by omitting the fixture override so adapters hit live ATS boards with throttled `max_jobs_per_source`.

### Data & Mocking Strategy
- **Fixture Layout**: Create `tests/fixtures/end_validation/sample_jobs.yaml` containing per-source job arrays with fields matching `RawJob` (external_id, title, company, location, description, url, posted_at, updated_at). Include:
  - At least one job matching all criteria.
  - One job filtered by `exclude_terms`.
  - One job that changes between runs (updated content hash).
  - Metadata for location filtering (remote vs on-site) to reflect PRD’s remote preference.
- **Fixture Adapter**: Implement a lightweight helper (either within the test module or under `tests/helpers/adapters.py`) that, given a `SourceConfig`, returns `RawJob` instances built from the YAML fixture. `pytest` tests monkeypatch `app.pipeline.runner.get_adapter` to return this helper.
- **SMTP Mocking**: Patch `app.notifications.smtp_client.SMTPClient.send_email` to return success booleans while capturing the payload count for assertions. Use `caplog` to ensure `notification.batch complete` log records reference the expected numbers.
- **Config Fixtures**: Reuse or extend `integration_app_config` / `integration_env_config` fixtures to point at the fixture-backed sources and search criteria identical to user expectations (required terms, keyword groups, exclude list).

### Logging & Metrics Verification
- Use `caplog.at_level("INFO")` to capture records from `app.pipeline.runner`, `app.notifications.service`, and `app.logging.context`. Assert the presence of specific `record.__dict__` keys (`event`, `component`, `run_id`, `source_id`) and their values for start/complete events.
- Validate that per-source logs include fetched/matched counts, and adapter failures emit `source.run.failed` when simulated errors are injected.
- For the manual run test, ensure `service.manual_scan.completed` includes `duration_seconds`, `total_fetched`, and `had_errors`.
- The sample scan harness should write stdout summaries and optionally tee structured logs into `temp_scan.log` for manual inspection.

### Alert Deduplication Verification
- After each test run, open a session via `get_session()` and assert:
  - `alerts_sent` table row count matches the number of unique `(job_key, version_hash)` combinations that should have triggered notifications.
  - Re-running the pipeline without job changes leaves both `alerts_sent` and `JobRepository.last_seen_at` unchanged aside from timestamp refreshes.
  - When a job’s description changes, `JobRepository.content_hash` updates, `AlertRepository` stores a new version hash, and exactly one notification per affected source is recorded.
- Include assertions around notification skip reasons by inspecting `NotificationResult.status` and `NotificationResult.error`.

## Step-by-Step Implementation Guide
1. **Author Fixture Data**
   - Create `tests/fixtures/end_validation/sample_jobs.yaml` with three logical job groups (matching, excluded, updatable) for both Greenhouse and Lever sources.
   - Embed metadata (posted/updated timestamps, remote locations) to exercise timestamp parsing and remote-only preferences.
   - Document fixture schema at the top of the file for contributors.

2. **Build Fixture Adapters & Test Utilities**
   - Add a helper `FixtureAdapter` class inside `tests/integration/test_end_validation.py` (or `tests/helpers/fixture_adapter.py`) that loads the YAML once per session, converts entries to `RawJob`, and mimics adapter interface (`fetch_jobs`).
   - Provide pytest fixtures for `app_config`, `env_config`, `notification_service` (real instance), and `keyword_matcher` seeded with realistic criteria.
   - Expose a utility to run the pipeline twice with optional mutation callbacks so dedup vs update scenarios share setup logic.

3. **Implement Automated Tests**
   - `test_full_pipeline_sample_fixture`: patch `get_adapter` to return `FixtureAdapter`, patch SMTP send, run pipeline, assert metrics/persistence/logs.
   - `test_repeated_run_dedupes_alerts`: reuse setup, run pipeline twice, assert `total_notified==0` on second run plus log skip reasons.
   - `test_updated_job_triggers_single_additional_alert`: mutate fixture data between runs (e.g., change description text) to ensure only the modified job triggers new alerts.
   - `test_manual_run_emits_summary_logs`: monkeypatch `sys.argv`, `load_config`, `configure_logging`, and `ScanPipeline.run_once` to simulate manual CLI invocation, then assert exit code and logged metrics.

4. **Create Sample Scan Harness**
   - Add `scripts/run_sample_scan.py` that:
     - Loads `.env`, config YAML, and logging (JSON or key-value).
     - Accepts CLI flags for fixture directory vs real network.
     - When `--use-fixtures` is set, monkeypatches `get_adapter` with the `FixtureAdapter`.
     - Runs `ScanPipeline.run_once()` and prints a summary table (counts, dedup status, log path, SQLite path).
     - Exposes an environment variable guard (e.g., `END_VALIDATION_REAL_RUN=1`) to run against live ATS with built-in throttling and warns about API usage.

5. **Document Execution Instructions**
   - Update `README.md` (Validation section) referencing the new script and pytest targets so PMs know how to run the sample scan and interpret results.
   - Note required environment variables (SMTP host/port, ALERT_TO_EMAIL) and how to stub them with `mailhog` or other local SMTP fakes during validation.
   - Document cleanup steps for `data/sample_end_validation.db` to keep the repo tidy.

## Verification Checklist
- `pytest tests/integration/test_end_validation.py -k full_pipeline` — validates happy-path pipeline execution with fixture data.
- `pytest tests/integration/test_end_validation.py -k dedup` — proves alert deduplication across consecutive runs.
- `pytest tests/integration/test_end_validation.py -k updated_job` — confirms updated content triggers new alerts and logs.
- `pytest tests/integration/test_end_validation.py -k manual_run` — ensures CLI manual mode emits required logs and exit codes.
- `python scripts/run_sample_scan.py --config docs/sample_end_validation.yaml --fixtures tests/fixtures/end_validation` — manual scan using mock data (no network).
- Optional (with approval): `END_VALIDATION_REAL_RUN=1 python scripts/run_sample_scan.py --config config.yaml` — limited real endpoint validation; monitor logs for adapter warnings and ensure rate limits are respected.
