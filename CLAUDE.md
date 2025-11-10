# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Job Opportunity Scanner is an automated service that monitors Applicant Tracking System (ATS) APIs for configured companies, evaluates job postings against keyword rules, deduplicates results, and sends targeted email notifications for matching opportunities.

**Key Technologies:** Python 3.13+, SQLAlchemy, APScheduler, Jinja2, SMTP, Docker

## Development Commands

### Environment Setup
```bash
# Install dependencies with uv (preferred)
uv sync

# Install with pip
pip install -e .

# Install with dev dependencies
pip install -e ".[dev]"
```

### Running the Service
```bash
# Manual scan (one-time execution for testing)
python -m app.main --manual-run

# Run as daemon with scheduler
python -m app.main

# Custom config path
python -m app.main --config /path/to/config.yaml

# Debug logging
python -m app.main --log-level DEBUG
```

### Testing
```bash
# Run all tests
pytest

# Run tests with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_matching.py

# Run single test function
pytest tests/test_matching.py::test_required_terms_match

# Run tests matching pattern
pytest -k "test_greenhouse"
```

### Code Quality
```bash
# Lint with ruff
ruff check .

# Format with black
black --check .

# Type checking with mypy
mypy app
```

### Docker
```bash
# Build image
docker build -t job-opportunity-scanner:latest .

# Run in manual mode
docker run --rm \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/config.yaml:/app/config.yaml \
  --env-file .env \
  job-opportunity-scanner:latest \
  --manual-run

# Run in daemon mode
docker run -d \
  --name job-scanner \
  -v job_scanner_data:/app/data \
  -v $(pwd)/config.yaml:/app/config.yaml \
  --env-file .env \
  job-opportunity-scanner:latest

# View logs
docker logs -f job-scanner
```

## Architecture

### Pipeline Architecture
The service follows a modular pipeline architecture that processes each source sequentially:

1. **Configuration Loader** ([app/config/](app/config/)) - Loads and validates [config.yaml](config.yaml), parses duration strings, validates environment variables
2. **Scheduler Service** ([app/scheduler/](app/scheduler/)) - Orchestrates periodic execution using APScheduler with lock-based overlap prevention
3. **Scan Pipeline** ([app/pipeline/](app/pipeline/)) - Coordinates the entire scan lifecycle for all sources
4. **ATS Adapters** ([app/adapters/](app/adapters/)) - Source-specific implementations for Greenhouse, Lever, and Ashby APIs
5. **Normalization Layer** ([app/normalization/](app/normalization/)) - Converts raw API responses to unified domain models
6. **Persistence Layer** ([app/persistence/](app/persistence/)) - SQLite storage with change detection via content hashing
7. **Matching Engine** ([app/matching/](app/matching/)) - Applies keyword rules (required terms, OR groups, exclusions)
8. **Notification Service** ([app/notifications/](app/notifications/)) - Formats and sends SMTP emails with matched keyword highlights
9. **Logging** ([app/logging/](app/logging/)) - Structured event logging with contextual fields (run_id, source_id, etc.)

### Key Design Patterns

**Change Detection:** Jobs are tracked using `content_hash` (computed from title + description + location). When a job's content changes, a new version is detected and re-matched against criteria.

**Deduplication:** The `alerts_sent` table tracks `(job_key, version_hash)` pairs to prevent duplicate notifications for unchanged content.

**Error Resilience:** Adapter failures for one source don't abort the entire pipeline. Each source is processed independently with error logging.

**Lock-Based Scheduling:** A threading lock prevents overlapping pipeline runs. If a scan takes longer than the interval, the next run is skipped with a warning.

### Database Schema

**jobs table:**
- `job_key` (PK) - Composite key from source type, identifier, and external ID
- `content_hash` - SHA256 of normalized content for change detection
- `first_seen_at`, `last_seen_at` - Timestamps for tracking job lifecycle
- `posted_at`, `updated_at` - Source-provided timestamps

**sources table:**
- `source_identifier` (PK)
- `last_success_at`, `last_error_at` - Health tracking
- `error_message` - Last error for troubleshooting

**alerts_sent table:**
- `job_key` (PK)
- `version_hash` - Content hash at time of alert
- `sent_at` - Timestamp of notification

### Module Responsibilities

**[app/main.py](app/main.py:1)** - Entry point with CLI argument parsing, service bootstrapping, signal handling for graceful shutdown

**[app/adapters/base.py](app/adapters/base.py:1)** - Abstract base class with shared HTTP handling, HTML cleaning, timestamp parsing

**[app/pipeline/runner.py](app/pipeline/runner.py:1)** - `ScanPipeline` class that orchestrates fetch → normalize → persist → match → notify for each source

**[app/persistence/database.py](app/persistence/database.py:1)** - Database initialization, session management, schema creation with SQLite-specific configuration (foreign keys, WAL mode)

**[app/matching/engine.py](app/matching/engine.py:1)** - `KeywordMatcher` evaluates jobs against `required_terms` (AND), `keyword_groups` (OR within groups), and `exclude_terms`

**[app/notifications/service.py](app/notifications/service.py:1)** - `NotificationService` handles email formatting, SMTP delivery, retry logic, and alert recording

**[app/utils/highlighting.py](app/utils/highlighting.py:1)** - Highlights matched keywords in job descriptions using `**term**` markdown syntax for email readability

### Configuration Structure

The [config.yaml](config.yaml) follows this schema:
- `sources[]` - List of ATS sources with `name`, `type`, `identifier`, `enabled`
- `scan_interval` - Duration string (e.g., "15m", "PT15M")
- `search_criteria` - Object with `required_terms[]`, `keyword_groups[][]`, `exclude_terms[]`
- `email` (optional) - SMTP configuration overrides
- `logging` (optional) - Format and level overrides
- `advanced` (optional) - Adapter timeouts and limits

Environment variables (higher priority than config):
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` - Required for email
- `ALERT_TO_EMAIL` - Recipient for job alerts
- `DATABASE_URL` - Defaults to `sqlite:///./data/job_scanner.db`
- `LOG_LEVEL` - Overrides config logging level

## Development Guidelines

### Adding a New ATS Adapter

1. Create new file in [app/adapters/](app/adapters/) (e.g., `workday.py`)
2. Inherit from `BaseAdapter` and implement `fetch_jobs()` method
3. Return list of `RawJob` domain models with `id`, `title`, `description`, `location`, `url`, `posted_at`, `updated_at`
4. Register adapter in [app/adapters/factory.py](app/adapters/factory.py) `get_adapter()` function
5. Add test fixtures in [tests/test_adapters.py](tests/test_adapters.py) with recorded API responses
6. Update [config.example.yaml](config.example.yaml) with example source configuration

### Modifying Match Criteria Logic

All keyword matching logic is centralized in [app/matching/engine.py](app/matching/engine.py). The `KeywordMatcher.evaluate()` method:
- Normalizes text (lowercase, strips punctuation)
- Checks required terms (all must match)
- Checks keyword groups (at least one term per group)
- Checks exclusions (reject if any match)
- Returns `MatchResult` with `is_match`, `matched_required_terms`, `matched_groups`, `matched_exclusions`

When modifying match logic, update corresponding tests in [tests/test_matching.py](tests/test_matching.py).

### Testing Patterns

**Adapter Tests:** Use fixtures with recorded API responses (JSON files or inline) to avoid live API calls. Mock `requests.Session.request()` to return canned responses.

**Pipeline Tests:** Use in-memory SQLite database (`sqlite:///:memory:`) and mock adapters that return predictable `RawJob` lists.

**Notification Tests:** Mock SMTP client using `pytest-mock` to verify email content without sending real emails.

**Persistence Tests:** Each test gets a fresh in-memory database. Use fixtures to create sample jobs and verify change detection logic.

### Logging Best Practices

All logs include structured `extra` fields:
- `event` - Machine-readable event name (e.g., `pipeline.run.started`)
- `run_id` - UUID for correlating logs within a single scan
- `source_id`, `source_name`, `ats_type` - Source context
- `job_key` - Job identifier for job-specific events

Use `log_context()` context manager from [app/logging/context.py](app/logging/context.py) to set context fields that apply to all logs within a scope.

### Error Handling Strategy

**Adapter Errors:** Raise `AdapterError` subclasses (`AdapterHTTPError`, `AdapterTimeoutError`, `AdapterResponseError`). Pipeline catches these, logs them, and continues to next source.

**Configuration Errors:** Raise `ConfigurationError` with helpful error messages and `suggestions` list. These fail fast during startup.

**Database Errors:** Transient errors (locks) are retried. Schema errors fail fast with clear messages.

**Notification Errors:** Log but don't mark alert as sent. Job will be re-notified on next scan if it still matches.

## Common Troubleshooting

### Database Locked Errors
SQLite uses WAL mode but still has write concurrency limits. Ensure only one service instance runs per database file. In Docker, don't mount the same volume to multiple containers.

### Missing Environment Variables
Service validates required variables at startup and fails with `ConfigurationError`. Check `.env` file or Docker `--env-file` argument.

### Adapters Returning Empty Results
Enable debug logging (`--log-level DEBUG`) to see HTTP requests/responses. Verify company identifiers match ATS-specific requirements (e.g., Greenhouse slug vs. API token).

### Email Not Sending
Most common issues: wrong SMTP credentials, TLS not enabled, firewall blocking port 587. Gmail requires app-specific passwords, not account passwords.

## File Organization Conventions

- **Models:** Domain models in `app/domain/models.py`, adapter-specific models in each module
- **Exceptions:** Custom exceptions defined in each module's `exceptions.py`
- **Tests:** Mirror the `app/` structure in `tests/` with `test_` prefix
- **Fixtures:** Recorded API responses stored inline or in `tests/fixtures/`
- **Configuration:** Validation in `app/config/validators.py`, Pydantic models in `app/config/models.py`
