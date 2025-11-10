# Documentation Updates Technical Specification (Step 12)

## Overview
- Step 12 translates the finished implementation into actionable documentation so new operators can configure, run, and troubleshoot the Job Opportunity Scanner without opening the PRD.
- Deliverables focus on a single source of truth: `README.md` must guide setup, configuration, execution modes, and list known limitations that affect production readiness.
- Scope is limited to documentation edits; no source code changes are required unless follow-up issues are discovered during doc validation.

## Goals and Non-Goals
- **Goals**
  - Provide an intentional onboarding flow (prerequisites → environment setup → configuration → first run → ongoing operations).
  - Document every required configuration touchpoint (`config.yaml`, `.env`, CLI flags, Docker mounts) with copy-paste-ready commands.
  - Capture operational guardrails (log events to watch, DB locations, when to use sample harness) and explicit known limitations.
- **Non-Goals**
  - Changing runtime behaviour, CLI flags, or validation logic.
  - Authoring API/adapter deep-dives (those stay in `docs/job-opportunity-scanner-techspec.md`).
  - Building automation to enforce docs (linting/tests can stay as-is).

## Acceptance Criteria
- README includes clear **setup instructions** referencing the actual toolchain defined in `pyproject.toml` (Python 3.13+, `uv`, optional `pip`).
- README explains **configuration** end-to-end:
  - How to copy and modify `config.example.yaml` plus validation rules enforced by `app/config/models.py`.
  - How environment variables are loaded via `.env`/`load_dotenv` and which values are mandatory vs optional.
  - How to run `verify_config.py` and what success/failure looks like.
- README documents **run instructions** for:
  - Manual one-off scans (`python -m app.main --manual-run` and the `job-scanner` console script).
  - Scheduler/daemon mode (`SchedulerService`, default intervals, preventing overlapping runs).
  - Sample validation harness (`scripts/run_sample_scan.py`) and Docker workflows.
- README ends with a **Known Limitations** section that surfaces architectural constraints (single profile, SQLite single-writer, ATS coverage, etc.) so users understand tradeoffs.
- All instructions must be reproducible without reading the PRD; references to source files/modules are encouraged for credibility but no code snippets are included.

## Assumptions & Dependencies
- Runtime dependencies, entry points, and config schemas are already implemented (`pyproject.toml`, `app/main.py`, `app/config/*`, `app/pipeline/runner.py`).
- `.env` loading happens automatically (`from dotenv import load_dotenv` at the top of `app/main.py`), so docs should lean on `.env.example`.
- SQLite remains the storage layer; database helper functions guarantee directories exist (`app/persistence/database.py`), so docs can recommend safe volume mounting.
- Current README content (Quick Start, Docker, Validation) can be reorganized but not removed unless redundant.
- Adding tables or diagrams in README is acceptable if kept in Markdown.

## Current Documentation Assessment
- README already provides a general quick start and Docker guidance but lacks a cohesive onboarding flow that ties `.env`, `config.yaml`, and first-run validation together.
- Configuration rules (minimum scan interval, requirement for at least one keyword set, adapter identifier expectations) only exist implicitly in code/techspec; they should surface in README.
- Environment variable behaviour (multiple recipients via commas, LOG_LEVEL precedence, default DB path) is explained in `.env.example` but not summarized publicly.
- `verify_config.py` and `scripts/run_sample_scan.py` exist yet are barely referenced; they should become primary verification steps.
- There is no explicit Known Limitations section even though the PRD and implementation constrain ATS coverage, notification channels, and scale characteristics.

## Integration Points
- `README.md`: primary document to update; new sections will be inserted or reorganized here.
- `.env.example` & `.env`: authoritative list of runtime environment variables to summarise.
- `config.example.yaml` & `config.yaml`: demonstrate the YAML schema and provide copy-ready examples.
- `verify_config.py`: lightweight validator to mention under configuration.
- `app/main.py`: shows CLI flags (`--config`, `--manual-run`, `--log-level`) and `job-scanner` entry point; informs run instructions.
- `app/pipeline/runner.py` & `app/scheduler/service.py`: describe pipeline metrics, locking, and scheduling cadence that docs should explain.
- `app/config/models.py` & `app/config/duration.py`: authoritative source for validation rules (supported ATS types, keyword requirements, scan interval bounds).
- `app/config/environment.py`: specifies required env vars (`SMTP_HOST`, `SMTP_PORT`, `ALERT_TO_EMAIL`) and optional overrides.
- `app/persistence/database.py`: documents database location and single-writer expectations.
- `scripts/run_sample_scan.py` & `docs/sample_end_validation.yaml`: assets for manual validation instructions.
- `Dockerfile`: informs container build/run steps, required mounts, and entrypoint.

## Proposed README Structure & Content Requirements

### 1. Quick Setup & Environment Preparation
- Introduce a concise prerequisites table (Python 3.13+, macOS/Linux, `uv` or `pip`, SMTP credentials, Docker optional).
- Outline installation commands in order: clone repo → `uv sync` (preferred) → optional `pip install -e .`.
- Explicitly direct users to copy `.env.example` to `.env`, fill SMTP + recipient values, and note that `load_dotenv()` auto-loads them when running `app/main.py`.
- Call out default paths created by the app (`data/job_scanner.db`) and mention that the CLI will create them on first run.

### 2. Configuration Workflow (`config.yaml`)
- Provide a numbered checklist: copy `config.example.yaml`, define at least one enabled source, set `search_criteria`, optionally adjust `scan_interval`, `email`, `logging`, `advanced`.
- Summarize validation rules enforced by `app/config/models.py`:
  - Supported ATS types (`greenhouse`, `lever`, `ashby`).
  - Requirement that either `required_terms` or `keyword_groups` is non-empty and they cannot conflict with `exclude_terms`.
  - `scan_interval` accepted formats plus enforced range (5 minutes ≤ interval ≤ 24 hours per `validate_duration_range`).
- Describe how `load_config` searches for configs (`--config` flag > `config.yaml` in repo root > `config/config.yaml`) so users can organize configs cleanly.
- Introduce `verify_config.py` as the fast feedback tool: `python verify_config.py`, expected success output, and remediation tips on failure.
- Encourage storing sample configs (e.g., `docs/sample_end_validation.yaml`) for testing and linking to techspec for deeper schema explanation.

### 3. Environment Variables & Secrets
- Add a table summarizing each variable from `.env.example`/`EnvironmentConfig`:
  - Required: `SMTP_HOST`, `SMTP_PORT` (integer), `ALERT_TO_EMAIL` (supports comma-separated addresses).
  - Optional: `SMTP_USER`, `SMTP_PASS`, `SMTP_SENDER_NAME`, `LOG_LEVEL`, `DATABASE_URL`, `SCAN_INTERVAL` override.
- Mention validation nuances implemented in `app/config/environment.py` (port range, user/pass pairs, log level choices).
- Document precedence order for log level (CLI flag → env var → YAML).
- Remind users not to commit `.env` and reference `.env.example` as starter template.

### 4. Running Modes & Operational Commands
- Detail manual execution: `python -m app.main --manual-run --config ./config.yaml --log-level DEBUG` and the equivalent `job-scanner --manual-run`.
- Describe scheduler mode: running `python -m app.main` or invoking the `job-scanner` console script without `--manual-run` to start APScheduler with immediate first run; mention lock-based skip logic (from `ScanPipeline`).
- Explain runtime outputs:
  - Structured log events (`service.starting`, `pipeline.run.completed`, `notification.skip`) and where they originate (`app/logging`, pipeline, notifications).
  - Metrics produced by `PipelineRunResult` (fetched, normalized, upserted, matched, notified) so operators know what to expect.
- Include instructions for the sample validation harness:
  - Fixture mode (default) using `scripts/run_sample_scan.py --config docs/sample_end_validation.yaml`.
  - Real endpoint mode gated by `END_VALIDATION_REAL_RUN=1`.
  - Where the harness writes its DB (`data/sample_end_validation.db`) and how to inspect results with `sqlite3`.
- Mention database inspection commands (`sqlite3 data/job_scanner.db '.tables'`) and housekeeping (delete DB to reset state).

### 5. Docker Deployment & Runtime Layout
- Reuse existing Docker content but reorganize into subsections:
  - Build (`docker build -t job-opportunity-scanner:latest .`, optional build args from `Dockerfile`).
  - Required mounts/env: `-v job_scanner_data:/app/data`, `-v $(pwd)/config.yaml:/app/config.yaml`, `--env-file .env`.
  - Manual-run container invocation vs daemon mode and how to run with `job-scanner --manual-run`.
  - Health check expectations (defined in Dockerfile) and how to monitor via `docker logs`.
- Explain that SQLite is not safe for concurrent writers; emphasize "one container per volume" and mention `PRAGMA journal_mode=WAL` already enabled but does not allow multi-writer.
- Document how to override defaults (`DATABASE_URL`, `LOG_LEVEL`, `ENVIRONMENT`) at runtime.

### 6. Validation, Troubleshooting, and Support Artifacts
- Provide a troubleshooting matrix:
  - Configuration errors: point to `ConfigurationError` messages, `verify_config.py`, and log file paths (`temp_scan.log` if relevant, else stdout).
  - Email failures: remind to check SMTP credentials, TLS, and review `notification` log events.
  - Stalled scheduler: mention `SchedulerService` lock and how to restart gracefully (Ctrl+C, `docker restart`).
- Include quick references for running tests (`pytest`, `pytest -k`, `pytest tests/integration/test_end_validation.py`) so confident users can verify behaviour.
- Link to `docs/job-opportunity-scanner-techspec.md` for architecture deep dives without duplicating content.

### 7. Known Limitations (New Section)
Document the following constraints with short rationales and relevant modules:
1. **ATS Coverage** – only Greenhouse, Lever, and Ashby adapters exist (`app/config/models.ATSType`); other platforms need new adapters.
2. **Single Profile & User** – `config.yaml` supports one global `search_criteria`; multiple personas or concurrent configs require separate deployments.
3. **Email-Only Notifications** – `NotificationService` only targets SMTP; there is no Slack/SMS/webhook support in v1.0.
4. **SQLite Single-Writer** – `app/persistence/database.py` enables WAL but the service assumes one process/container per database file.
5. **No Hot Reload** – `load_config` reads configuration once at startup; apply changes by restarting the process/container.
6. **Deduplication Scope** – `job_key` includes the source identifier, so the same job published in multiple ATS instances may trigger multiple alerts.
7. **Network Dependencies** – Adapters rely on public ATS APIs with no scraping fallback; corporate firewalls or API changes can break scans.
8. **Location Filtering via Keywords** – There is no structured geolocation filter; remote-only or geography filters must be encoded in `search_criteria`.
9. **Outbound Email Requirement** – Service cannot operate without working SMTP credentials; there is no queue/retry beyond in-process attempts.
10. **Single-Process Scheduler** – APScheduler runs in-process without clustering; scaling horizontally requires external coordination and a shared database (not supported in v1.0).

## Implementation Plan
1. **Outline & ToC** – Update `README.md` to include a short table of contents and the new section headings described above.
2. **Setup Section** – Rewrite the current “Quick Start” block into “Setup & Installation,” ensuring it covers cloning, dependency installation, `.env` creation, and directory expectations.
3. **Configuration Section** – Insert a detailed “Configure `config.yaml`” section with validation notes, config search order, and `verify_config.py` usage. Reference `config.example.yaml` and highlight minimum requirements.
4. **Environment Variables Table** – Add a Markdown table summarizing required/optional env vars, default values, validation notes, and where each is consumed in the codebase.
5. **Running the Scanner** – Consolidate manual run, scheduler mode, sample harness, database inspection, and log expectations into a cohesive section with clearly labeled subsections.
6. **Docker Deployment** – Restructure the Docker portion to align with the new outline, emphasizing mounts, env files, and single-container guidance; remove redundant snippets if necessary.
7. **Validation & Troubleshooting** – Add sub-sections for `verify_config.py`, `pytest`, `scripts/run_sample_scan.py`, and log-based debugging.
8. **Known Limitations** – Append the new limitations section near the end of README before License/Contributing, using concise bullets with references to relevant modules/files.
9. **Cross-Checks & Links** – Ensure all new sections link to supporting docs (`docs/job-opportunity-scanner-techspec.md`, `docs/sample_end_validation.yaml`) and that command examples prefer repo-relative paths.
10. **Review** – Proofread for accuracy, ensure no instructions refer to undefined scripts, and verify that copy-paste commands succeed locally (clone, install, run manual scan).

## Validation & Sign-off
- Manually follow the documented steps on a fresh checkout (or simulated commands) to ensure no typos or missing prerequisites.
- Run `python verify_config.py` and `python -m app.main --manual-run --config config.yaml` using the updated instructions to confirm they align with reality.
- For Docker instructions, `docker run --rm ... --manual-run` should finish successfully on a sample config; note in README how to confirm success (log line `service.manual_scan.completed`).
- Confirm the Known Limitations list matches PRD expectations and implementation realities; update if new constraints emerge during review.

## Open Questions & Assumptions
- Do we want a full table of contents at the top of README or rely on GitHub’s auto-generated section links? (Assume manual ToC is acceptable unless product prefers otherwise.)
- Should we mention Windows support explicitly? Current tooling assumes Unix-like shells; unless testing proves Windows compatibility, state that Windows requires WSL.
- If future adapters or notification channels are planned, note that the Known Limitations list will need updates; treat it as living documentation.
