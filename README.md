# Job Opportunity Scanner

An automated service that monitors Applicant Tracking System (ATS) APIs for configured companies, evaluates job postings against keyword rules, deduplicates results, and sends targeted email notifications for matching opportunities.

## Features

- **Multi-ATS Support**: Integrates with Greenhouse, Lever, and Ashby job posting systems
- **Keyword-Based Filtering**: Define required terms, OR groups, and exclusion lists for precise job matching
- **Deduplication**: Intelligent change detection prevents duplicate notifications
- **Email Notifications**: Receive alerts for matching job postings with matched keyword highlights
- **Scheduled Scanning**: Configurable polling interval (5 minutes to 24 hours)
- **SQLite Persistence**: Local database for job history and alert tracking
- **Docker Ready**: Containerized deployment with environment-based configuration
- **Observability**: Structured logging for monitoring and troubleshooting

## Table of Contents

- [Quick Setup & Prerequisites](#quick-setup--prerequisites)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Application Config (config.yaml)](#application-config-configyaml)
  - [Configuration Validation](#configuration-validation)
- [Running the Scanner](#running-the-scanner)
  - [Manual Mode](#manual-mode-one-off-scans)
  - [Daemon Mode](#daemon-mode-continuous-scanning)
  - [Sample Validation Harness](#sample-validation-harness)
  - [Pipeline Metrics](#pipeline-metrics)
- [Docker Deployment](#docker-deployment)
- [Development & Testing](#development--testing)
- [Project Structure](#project-structure)
- [Architecture](#architecture)
- [Troubleshooting](#troubleshooting)
- [Known Limitations](#known-limitations)
- [License & Contributing](#license--contributing)

## Quick Setup & Prerequisites

### Prerequisites

| Requirement | Version/Details | Notes |
|-------------|----------------|-------|
| **Python** | 3.13 or higher | Required for running natively |
| **Operating System** | macOS, Linux, or WSL | Windows requires WSL (Windows Subsystem for Linux) |
| **SMTP Server** | Any SMTP-compatible email provider | Gmail, SendGrid, Outlook, corporate mail server |
| **Docker** | Latest version | Optional, for containerized deployment |
| **uv** | Latest version | Optional, recommended for faster dependency management |

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/job-opportunity-scanner.git
   cd job-opportunity-scanner
   ```

2. **Install dependencies** (choose one method):

   **Option A: Using `uv` (recommended - faster):**
   ```bash
   uv sync
   ```

   **Option B: Using `pip`:**
   ```bash
   pip install -e .
   ```

   **Option C: With development dependencies:**
   ```bash
   pip install -e ".[dev]"
   ```

3. **Create environment file:**
   ```bash
   cp .env.example .env
   # Edit .env with your SMTP credentials (see Environment Variables section below)
   ```

   The application automatically loads `.env` on startup via `load_dotenv()`.

4. **Create configuration file:**
   ```bash
   cp config.example.yaml config.yaml
   # Edit config.yaml with your ATS sources and search criteria
   ```

**Note:** The `data/` directory for the SQLite database will be created automatically on first run.

## Configuration

### Environment Variables

The scanner requires several environment variables for SMTP authentication and optionally allows runtime overrides. Copy `.env.example` to `.env` and configure the following:

| Variable | Required | Default | Description | Validation |
|----------|----------|---------|-------------|------------|
| `SMTP_HOST` | **Yes** | - | SMTP server hostname | Non-empty string (e.g., `smtp.gmail.com`) |
| `SMTP_PORT` | **Yes** | - | SMTP server port | Integer 1-65535 (e.g., `587` for TLS, `465` for SSL) |
| `ALERT_TO_EMAIL` | **Yes** | - | Email recipient(s) for job alerts | Valid email format; supports comma-separated list |
| `SMTP_USER` | No* | - | SMTP authentication username | Must be set with `SMTP_PASS` or both omitted |
| `SMTP_PASS` | No* | - | SMTP authentication password | Must be set with `SMTP_USER` or both omitted |
| `SMTP_SENDER_NAME` | No | "Job Opportunity Scanner" | Display name for sender | Any string |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `DATABASE_URL` | No | `sqlite:///./data/job_scanner.db` | Database connection string | Valid SQLAlchemy URL |
| `SCAN_INTERVAL` | No | (from config.yaml) | Override scan interval | Duration string (e.g., `15m`, `PT15M`) |

*Note: `SMTP_USER` and `SMTP_PASS` must both be provided together or both omitted (for unauthenticated SMTP servers).

**Multi-Email Support:** The `ALERT_TO_EMAIL` variable supports comma-separated email addresses:
```bash
ALERT_TO_EMAIL="user1@example.com,user2@example.com,team@example.com"
```

**Configuration Precedence for Overrides:**
- **Log Level**: CLI `--log-level` flag → `LOG_LEVEL` env var → `logging.level` in config.yaml
- **Scan Interval**: `SCAN_INTERVAL` env var → `scan_interval` in config.yaml

**Example Providers:**

<details>
<summary><strong>Gmail</strong></summary>

```bash
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
SMTP_USER="your-email@gmail.com"
SMTP_PASS="your-app-password"  # Generate at https://myaccount.google.com/apppasswords
ALERT_TO_EMAIL="alerts@example.com"
```
</details>

<details>
<summary><strong>SendGrid</strong></summary>

```bash
SMTP_HOST="smtp.sendgrid.net"
SMTP_PORT="587"
SMTP_USER="apikey"
SMTP_PASS="your-sendgrid-api-key"
ALERT_TO_EMAIL="alerts@example.com"
```
</details>

<details>
<summary><strong>Outlook/Office 365</strong></summary>

```bash
SMTP_HOST="smtp.office365.com"
SMTP_PORT="587"
SMTP_USER="your-email@outlook.com"
SMTP_PASS="your-password"
ALERT_TO_EMAIL="alerts@example.com"
```
</details>

### Application Config (config.yaml)

The main configuration file defines ATS sources, search criteria, and operational settings.

**Setup Steps:**

1. Copy the example configuration:
   ```bash
   cp config.example.yaml config.yaml
   ```

2. Define at least one enabled ATS source
3. Configure search criteria (required terms, keyword groups, or both)
4. Optionally adjust scan interval, email settings, logging, and advanced options
5. Validate your configuration (see [Configuration Validation](#configuration-validation))

**Configuration File Search Order:**

The scanner looks for configuration in this order:
1. Explicit path via `--config` flag (e.g., `--config /path/to/config.yaml`)
2. `config.yaml` in the current directory
3. `config/config.yaml` subdirectory

This allows organizing multiple configs: `config/production.yaml`, `config/staging.yaml`, etc.

**Example Configuration:**

```yaml
sources:
  - name: "MyCompany Careers"
    type: "greenhouse"           # Options: greenhouse, lever, ashby
    identifier: "mycompany"      # Company-specific identifier
    enabled: true                # Optional, defaults to true

scan_interval: "15m"             # Polling frequency (5min - 24hrs)

search_criteria:
  required_terms:                # All terms must match (AND logic)
    - "python"
    - "backend"
  keyword_groups:                # At least one term per group (OR within groups)
    - ["remote", "work-from-home"]
    - ["senior", "lead", "staff"]
  exclude_terms:                 # Exclude jobs containing these terms
    - "django"
    - "legacy"

email:                           # Optional email configuration
  use_tls: true                  # Default: true
  max_retries: 3                 # Range: 0-10, default: 3
  retry_backoff_multiplier: 2.0  # Range: 1.0-5.0
  retry_initial_delay: 5         # Seconds, range: 1-60

logging:                         # Optional logging configuration
  level: "INFO"                  # DEBUG, INFO, WARNING, ERROR, CRITICAL
  format: "key-value"            # Options: json, key-value

advanced:                        # Optional advanced settings
  http_request_timeout: 30       # Seconds, range: 5-300
  max_jobs_per_source: 1000      # 0 = unlimited
```

**Validation Rules:**

The following rules are enforced by [`app/config/models.py`](app/config/models.py):

| Rule | Constraint | Module Reference |
|------|------------|------------------|
| **ATS Types** | Only `greenhouse`, `lever`, `ashby` supported | `ATSType` enum |
| **Search Criteria** | At least one of `required_terms` or `keyword_groups` must be non-empty | `SearchCriteria.model_validate()` |
| **Term Conflicts** | Same term cannot appear in both `required_terms` and `exclude_terms` | `SearchCriteria.check_conflicts()` |
| **Keyword Group Conflicts** | Terms in `keyword_groups` cannot be in `exclude_terms` | `SearchCriteria.check_conflicts()` |
| **Scan Interval** | Minimum: 5 minutes, Maximum: 24 hours | `validate_duration_range()` |
| **Enabled Sources** | At least one source must have `enabled: true` | `AppConfig.check_enabled_sources()` |
| **Duplicate Sources** | No two sources with same `type` + `identifier` combination | `AppConfig.check_duplicate_sources()` |
| **Email Retries** | Range: 0-10 retries | `EmailConfig` |
| **HTTP Timeout** | Range: 5-300 seconds | `AdvancedConfig` |

**Duration Formats:**

The `scan_interval` field accepts two formats:

**Human-Readable:**
- `15m` = 15 minutes
- `1h` = 1 hour
- `1h30m` = 1 hour 30 minutes
- `2d` = 2 days

**ISO-8601:**
- `PT15M` = 15 minutes
- `PT1H` = 1 hour
- `PT1H30M` = 1 hour 30 minutes
- `P2D` = 2 days

**Case-Insensitive Matching:**

All keyword terms are normalized to lowercase for matching. Whitespace is stripped. This means:
- `Python` = `python` = `PYTHON`
- `"  backend  "` = `"backend"`

**Additional Notes:**

- Source names and identifiers must be non-empty after whitespace stripping
- Multiple keyword groups use OR logic within each group and AND logic between groups
- For detailed schema and architecture, see [`docs/job-opportunity-scanner-techspec.md`](docs/job-opportunity-scanner-techspec.md)

### Configuration Validation

Before running the scanner for the first time, validate your configuration to catch errors early.

**Run the validation script:**

```bash
python verify_config.py
```

**Expected output on success:**

```
✓ Configuration file loaded successfully
✓ Found 2 sources
✓ All sources have valid types (greenhouse, lever, ashby)
✓ Search criteria defined (2 required terms, 2 keyword groups)
✓ No conflicts between required and excluded terms
✓ Configuration is valid!

Summary:
  Sources: 2
  Enabled: 2
  Required Terms: 2
  Keyword Groups: 2
  Exclude Terms: 2
```

**Common validation errors and fixes:**

| Error Message | Fix |
|---------------|-----|
| `At least one of required_terms or keyword_groups must be provided` | Add terms to either field in `search_criteria` |
| `Term 'python' cannot be both required and excluded` | Remove the term from one of the lists |
| `Invalid ATS type 'workday'` | Only `greenhouse`, `lever`, `ashby` are supported |
| `scan_interval must be between 5 minutes and 24 hours` | Adjust to valid range (e.g., `15m` instead of `2m`) |
| `At least one source must be enabled` | Set `enabled: true` on at least one source |

After fixing errors, re-run `verify_config.py` before attempting to run the scanner.

## Running the Scanner

### Manual Mode (One-Off Scans)

Manual mode executes a single scan and exits immediately. Useful for testing, troubleshooting, or running via cron.

**Using the console script (recommended):**

```bash
job-scanner --manual-run
```

**Or using the Python module:**

```bash
python -m app.main --manual-run
```

**With custom configuration:**

```bash
job-scanner --manual-run --config /path/to/config.yaml
```

**With debug logging:**

```bash
job-scanner --manual-run --log-level DEBUG
```

**Exit Codes:**

- `0` = Success (scan completed without errors)
- `1` = Failure (configuration error, adapter failure, or other exception)

**Use Cases:**

- Testing configuration changes before deploying
- Debugging specific ATS adapter issues
- Running periodic scans via cron instead of daemon mode
- CI/CD pipeline validation

### Daemon Mode (Continuous Scanning)

Daemon mode runs continuously with the scheduler, executing scans at the configured interval.

**Start the scheduler:**

```bash
job-scanner
```

**Or:**

```bash
python -m app.main
```

**Scheduler Behavior:**

1. **Immediate first run**: Executes scan immediately on startup
2. **Interval-based repetition**: Subsequent scans run at `scan_interval` intervals
3. **Overlap prevention**: Lock-based mechanism prevents concurrent scans if a scan exceeds the interval
4. **Graceful shutdown**: Responds to SIGINT (Ctrl+C) and SIGTERM signals

**Stopping the Scheduler:**

```bash
# Graceful shutdown
Ctrl+C

# Or send SIGTERM
kill <pid>
```

**Log Events to Monitor:**

Key structured log events emitted during operation (defined in `app/logging`, `app/main.py`, `app/pipeline/runner.py`):

| Event | Description | Module |
|-------|-------------|--------|
| `service.starting` | Service initialization beginning | `app/main.py` |
| `config.loaded` | Configuration loaded and validated | `app/main.py` |
| `services.initialized` | Pipeline services created | `app/main.py` |
| `daemon_mode.started` | Scheduler started in daemon mode | `app/main.py` |
| `pipeline.run.started` | Scan pipeline execution beginning | `app/pipeline/runner.py` |
| `pipeline.run.completed` | Scan pipeline finished | `app/pipeline/runner.py` |
| `source.run.started` | Processing individual ATS source | `app/pipeline/runner.py` |
| `source.run.completed` | Source processing finished | `app/pipeline/runner.py` |
| `notification.skip` | Alert skipped (already sent) | `app/notifications/service.py` |
| `service.stopping` | Graceful shutdown initiated | `app/main.py` |

**Preventing Overlapping Runs:**

The scheduler uses a threading lock (`app/scheduler/service.py`) to prevent concurrent scans. If a scan takes longer than the interval, the next scheduled run is skipped with a warning log. Adjust `scan_interval` if this occurs frequently.

### Sample Validation Harness

For manual validation without pytest (useful for stakeholders or pre-deployment testing):

**Run with fixture data (no network required):**

```bash
python scripts/run_sample_scan.py --config docs/sample_end_validation.yaml
```

**Custom database location:**

```bash
python scripts/run_sample_scan.py --config docs/sample_end_validation.yaml --database /tmp/validation.db
```

**Run with real ATS endpoints (requires network):**

⚠️ **Use with caution** - makes actual HTTP requests to ATS APIs

```bash
export END_VALIDATION_REAL_RUN=1
python scripts/run_sample_scan.py --config config.yaml
# The script will prompt for confirmation before proceeding
```

**What the Harness Does:**

- **Fixture Mode (default)**: Uses deterministic test data from `tests/fixtures/end_validation/sample_jobs.yaml`
- **Real Endpoint Mode**: Connects to live ATS APIs (when `END_VALIDATION_REAL_RUN=1`)
- **Summary Output**: Prints formatted table with metrics (fetched, matched, notified)
- **Database Creation**: Writes results to `data/sample_end_validation.db` (or custom path)
- **SMTP Mocking**: In fixture mode, emails are mocked (not sent)

**Expected Results with Fixture Data:**

- 9 jobs fetched (5 from Test Company A, 4 from Test Company B)
- 5 jobs matched against search criteria
- 5 notifications sent (one per matched job)

**Inspecting Results:**

```bash
# View tables
sqlite3 data/sample_end_validation.db '.tables'

# View job titles and locations
sqlite3 data/sample_end_validation.db 'SELECT title, location FROM jobs;'

# View sent alerts
sqlite3 data/sample_end_validation.db 'SELECT * FROM alerts_sent;'

# Clean up
rm data/sample_end_validation.db
```

### Pipeline Metrics

Each scan produces a `PipelineRunResult` with the following metrics (defined in `app/pipeline/models.py`):

| Metric | Description |
|--------|-------------|
| `total_fetched` | Jobs retrieved from all ATS sources |
| `total_normalized` | Jobs successfully normalized to domain model |
| `total_upserted` | Jobs inserted or updated in database |
| `total_matched` | Jobs matching search criteria |
| `total_notified` | Notifications sent (excludes duplicates) |
| `total_errors` | Count of errors encountered |
| `alerts_sent` | Count of alert records created |
| `had_errors` | Boolean indicating if any errors occurred |
| `total_duration_seconds` | Total execution time |
| `source_stats` | Per-source breakdown of above metrics |

These metrics appear in log output and can be monitored for operational insights.

## Docker Deployment

### Building the Image

**Basic build:**

```bash
docker build -t job-opportunity-scanner:latest .
```

**With BuildKit (improved caching):**

```bash
DOCKER_BUILDKIT=1 docker build -t job-opportunity-scanner:latest .
```

**With version and build metadata:**

```bash
docker build \
  --build-arg APP_VERSION=1.0.0 \
  --build-arg BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ') \
  -t job-opportunity-scanner:latest .
```

### Running Containers

The container uses the `job-scanner` console script as its entrypoint (not `python -m app.main`).

**Scheduler mode (default - runs continuously):**

```bash
docker run -d \
  --name job-scanner \
  -v job_scanner_data:/app/data \
  -v $(pwd)/config.yaml:/app/config.yaml \
  --env-file .env \
  job-opportunity-scanner:latest
```

**Manual run mode (one-time scan):**

```bash
docker run --rm \
  -v job_scanner_data:/app/data \
  -v $(pwd)/config.yaml:/app/config.yaml \
  --env-file .env \
  job-opportunity-scanner:latest \
  --manual-run
```

**With explicit environment variables:**

```bash
docker run -d \
  --name job-scanner \
  -v job_scanner_data:/app/data \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -e SMTP_HOST="smtp.gmail.com" \
  -e SMTP_PORT="587" \
  -e SMTP_USER="your-email@gmail.com" \
  -e SMTP_PASS="your-app-password" \
  -e ALERT_TO_EMAIL="alerts@example.com" \
  -e LOG_LEVEL="INFO" \
  job-opportunity-scanner:latest
```

**With bind mount for local development:**

```bash
docker run -d \
  --name job-scanner \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/config.yaml:/app/config.yaml \
  --env-file .env \
  job-opportunity-scanner:latest
```

### Required Mounts and Environment

**Required Mounts:**

| Mount | Container Path | Purpose |
|-------|----------------|---------|
| **Data Volume** | `/app/data` | SQLite database persistence |
| **Config File** | `/app/config.yaml` | Application configuration |

**Required Environment Variables:**

As documented in [Environment Variables](#environment-variables) section:
- `SMTP_HOST`
- `SMTP_PORT`
- `ALERT_TO_EMAIL`

Missing required variables will cause startup failure with `ConfigurationError`.

### Health Checks and Monitoring

**Built-in Health Check:**

The Dockerfile defines a health check (5-minute interval) that monitors the process:

```dockerfile
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f job-scanner || exit 1
```

**View health status:**

```bash
docker inspect --format='{{.State.Health.Status}}' job-scanner
```

**Monitor logs:**

```bash
# Follow logs in real-time
docker logs -f job-scanner

# View last 100 lines
docker logs --tail 100 job-scanner

# View logs with timestamps
docker logs -t job-scanner
```

**Key log events to watch:**

- `service.starting` - Service initialization
- `service.manual_scan.completed` - Manual scan finished (check `had_errors`)
- `pipeline.run.completed` - Scheduled scan finished (monitor metrics)
- Errors and warnings for troubleshooting

### Data Persistence and SQLite

**Create named volume (recommended for production):**

```bash
docker volume create job_scanner_data
docker run -v job_scanner_data:/app/data ...
```

**Bind mount (useful for development/inspection):**

```bash
docker run -v $(pwd)/data:/app/data ...
```

⚠️ **Critical: SQLite Single-Writer Limitation**

SQLite with WAL mode (`app/persistence/database.py`) improves concurrency but **does not support multiple writers**. **Run only one container per volume.** Multiple containers writing to the same database will cause corruption.

**Inspecting the database:**

```bash
# Using a temporary container
docker run --rm -it \
  -v job_scanner_data:/app/data \
  python:3.13-slim \
  sqlite3 /app/data/job_scanner.db

# Inside SQLite shell
.tables
SELECT * FROM jobs LIMIT 10;
SELECT * FROM alerts_sent;
.quit
```

### Resource Limits and Optimization

**Recommended limits for production:**

```bash
docker run -d \
  --name job-scanner \
  --memory=512m \
  --cpus=1 \
  -v job_scanner_data:/app/data \
  -v $(pwd)/config.yaml:/app/config.yaml \
  --env-file .env \
  job-opportunity-scanner:latest
```

### Container Lifecycle Management

**Stop and remove:**

```bash
# Stop gracefully
docker stop job-scanner

# Remove container
docker rm job-scanner

# Stop and remove in one command
docker rm -f job-scanner
```

**Volume management:**

```bash
# List volumes
docker volume ls

# Remove volume (deletes all job history)
docker volume rm job_scanner_data
```

The data volume persists even after container removal, preserving job history and alert records.

## Development & Testing

### Install Development Dependencies

```bash
pip install -e ".[dev]"
```

This installs additional tools: pytest, pytest-cov, pytest-mock, black, ruff, mypy

### Running Tests

**All tests:**

```bash
pytest
```

**With coverage:**

```bash
pytest --cov=app --cov-report=html
# View: open htmlcov/index.html
```

**Specific test module:**

```bash
pytest tests/test_matching.py
```

**Pattern matching:**

```bash
pytest -k "test_greenhouse"
pytest -k "integration"
```

**End-to-end validation tests:**

```bash
pytest tests/integration/test_end_validation.py -v
```

These tests validate:
- Complete pipeline execution (fetch → normalize → persist → match → notify)
- Alert deduplication (no duplicate notifications for unchanged jobs)
- Change detection (updated jobs trigger exactly one new alert)
- CLI manual mode with proper exit codes

Test fixtures include recorded API responses for each ATS platform to enable offline testing.

### Code Quality Tools

**Linting:**

```bash
ruff check .
```

**Formatting:**

```bash
black --check .  # Check only
black .          # Format files
```

**Type checking:**

```bash
mypy app
```

## Project Structure

```
job-opportunity-scanner/
├── app/                      # Main application package
│   ├── config/              # Configuration management
│   ├── adapters/            # ATS-specific adapters (Greenhouse, Lever, Ashby)
│   ├── normalization/       # Data normalization layer
│   ├── persistence/         # Database operations (SQLite)
│   ├── matching/            # Keyword matching engine
│   ├── scheduler/           # Task scheduling (APScheduler)
│   ├── notifications/       # Email notification service (SMTP)
│   ├── logging/             # Structured logging configuration
│   ├── utils/               # Utility functions
│   └── main.py              # Application entry point and CLI
├── tests/                    # Test suite
│   ├── integration/         # Integration tests
│   ├── fixtures/            # Test fixtures and recorded API responses
│   └── helpers/             # Test utilities
├── scripts/                  # Utility scripts
│   └── run_sample_scan.py   # Manual validation harness
├── data/                     # SQLite database storage (created on first run)
├── docs/                     # Documentation
│   ├── job-opportunity-scanner-techspec.md  # Architecture details
│   └── sample_end_validation.yaml           # Sample validation config
├── config.example.yaml       # Example configuration
├── .env.example              # Example environment variables
├── verify_config.py          # Configuration validation script
├── Dockerfile                # Container build definition
├── pyproject.toml            # Project configuration and dependencies
└── README.md                 # This file
```

## Architecture

The service follows a modular pipeline architecture:

1. **Configuration Loader** ([`app/config/loader.py`](app/config/loader.py)) → Reads and validates YAML configuration
2. **Scheduler Service** ([`app/scheduler/service.py`](app/scheduler/service.py)) → Orchestrates periodic execution with APScheduler
3. **ATS Adapters** ([`app/adapters/`](app/adapters/)) → Fetch job postings from Greenhouse, Lever, Ashby
4. **Normalization Layer** ([`app/normalization/service.py`](app/normalization/service.py)) → Convert to unified domain model
5. **Persistence Layer** ([`app/persistence/`](app/persistence/)) → SQLite storage and change detection via content hashing
6. **Matching Engine** ([`app/matching/engine.py`](app/matching/engine.py)) → Apply keyword rules and evaluate jobs
7. **Notification Service** ([`app/notifications/service.py`](app/notifications/service.py)) → Send email alerts via SMTP
8. **Logging** ([`app/logging/`](app/logging/)) → Structured event emission for observability

For detailed architecture documentation, design patterns, and data flow diagrams, see [`docs/job-opportunity-scanner-techspec.md`](docs/job-opportunity-scanner-techspec.md).

## Troubleshooting

### Configuration Errors

**Symptoms:**
- `ConfigurationError` on startup
- Validation errors from `verify_config.py`

**Solutions:**
1. Run `python verify_config.py` to identify specific issues
2. Check error message details (includes suggestions for fixes)
3. Review [Configuration Validation](#configuration-validation) section
4. Ensure `.env` file exists and contains required variables
5. Verify `config.yaml` follows schema in `config.example.yaml`

**Common issues:**
- Missing required environment variables (`SMTP_HOST`, `SMTP_PORT`, `ALERT_TO_EMAIL`)
- Invalid ATS type (only `greenhouse`, `lever`, `ashby` supported)
- Scan interval out of range (must be 5 minutes to 24 hours)
- Conflicting terms in `required_terms` and `exclude_terms`

### Email Not Sending

**Symptoms:**
- No email alerts received
- `notification.failed` log events
- SMTP authentication errors

**Solutions:**
1. Verify SMTP credentials in `.env` file
2. Check TLS/SSL port (587 for TLS, 465 for SSL)
3. Enable debug logging: `--log-level DEBUG`
4. Review `notification.*` log events for specific errors
5. For Gmail: Generate app-specific password at https://myaccount.google.com/apppasswords
6. Test SMTP connection separately before running scanner

**Log events to check:**
- `notification.batch.complete` - Shows sent/failed counts
- `notification.send.failed` - Contains error details

### No Jobs Found

**Symptoms:**
- `total_fetched: 0` in pipeline metrics
- Empty scan results

**Solutions:**
1. Verify company identifiers in `config.yaml` are correct for each ATS
   - Greenhouse: Company slug from URL (e.g., `company` in `boards.greenhouse.io/company`)
   - Lever: Company identifier from URL (e.g., `company` in `jobs.lever.co/company`)
   - Ashby: API token or identifier
2. Check if company's careers page is accessible from your network
3. Review logs for adapter errors and HTTP status codes
4. Enable debug logging to see API request/response details: `--log-level DEBUG`
5. Test with manual run first: `job-scanner --manual-run --log-level DEBUG`

### Database Lock Errors

**Symptoms:**
- `database is locked` errors
- `OperationalError` from SQLAlchemy

**Solutions:**
1. Ensure only one instance is running at a time
2. Check for stale processes: `ps aux | grep job-scanner`
3. Delete `.db-journal` file if present: `rm data/job_scanner.db-journal`
4. Restart the scanner
5. In Docker: Verify only one container uses each volume

⚠️ SQLite with WAL mode supports concurrent reads but **only one writer**. Do not run multiple instances against the same database.

### Scheduler Stalled or Skipped Runs

**Symptoms:**
- Log shows "Previous scan still in progress, skipping"
- Scans not executing at expected intervals

**Solutions:**
1. Check if scan duration exceeds `scan_interval`
2. Review `total_duration_seconds` in pipeline metrics
3. Increase `scan_interval` if scans consistently take longer
4. Check `advanced.http_request_timeout` setting (default 30s)
5. Reduce number of sources or `max_jobs_per_source` if network is slow
6. Restart scheduler to clear any stuck locks: Ctrl+C then restart

**Lock-based behavior:**

The scheduler ([`app/scheduler/service.py`](app/scheduler/service.py)) uses a threading lock to prevent overlapping scans. If a scan takes longer than the interval, subsequent runs are skipped until the current scan completes.

### Log-Based Debugging

**Enable debug logging:**

```bash
job-scanner --log-level DEBUG
```

**Key log fields to examine:**

- `event` - Machine-readable event identifier
- `component` - Module emitting the log
- `run_id` - UUID correlating logs from single scan
- `source_id` - ATS source being processed
- `error` - Error details if present

**Filter logs by component:**

```bash
# In structured logs, grep by component
docker logs job-scanner | grep '"component":"adapter"'
docker logs job-scanner | grep '"component":"notification"'
```

## Known Limitations

This section documents architectural constraints and design tradeoffs that affect deployment and usage:

### 1. ATS Coverage

**Limitation:** Only Greenhouse, Lever, and Ashby adapters are implemented.

**Impact:** Organizations using other ATS platforms (Workday, iCIMS, SmartRecruiters, etc.) cannot be monitored without custom adapter development.

**Module:** [`app/config/models.py`](app/config/models.py) (`ATSType` enum), [`app/adapters/`](app/adapters/)

**Workaround:** Implement new adapters following the `BaseAdapter` pattern in [`app/adapters/base.py`](app/adapters/base.py).

### 2. Single Profile & Search Criteria

**Limitation:** One global `search_criteria` per deployment. No support for multiple user profiles or persona-based filtering.

**Impact:** Teams with different search needs (e.g., backend vs frontend roles) must run separate scanner instances with different configs.

**Module:** [`app/config/models.py`](app/config/models.py) (`SearchCriteria`)

**Workaround:** Deploy multiple instances with distinct `config.yaml` files and database paths.

### 3. Email-Only Notifications

**Limitation:** `NotificationService` only supports SMTP email delivery. No Slack, SMS, webhooks, or other notification channels.

**Impact:** Teams preferring non-email alerts must build custom notification integrations.

**Module:** [`app/notifications/service.py`](app/notifications/service.py)

**Workaround:** Fork notification service or implement webhook endpoints that forward emails to other systems.

### 4. SQLite Single-Writer Constraint

**Limitation:** SQLite with WAL mode supports concurrent reads but only one writer process. The database does not support horizontal scaling.

**Impact:** Cannot run multiple scanner instances against the same database file. Docker deployments must use one container per volume.

**Module:** [`app/persistence/database.py`](app/persistence/database.py)

**Workaround:** Use separate database files for each instance, or migrate to PostgreSQL/MySQL (requires code changes).

### 5. No Hot Reload of Configuration

**Limitation:** `load_config` reads configuration once at startup. Changes to `config.yaml` or `.env` require restarting the process.

**Impact:** Configuration updates cause brief downtime during restart.

**Module:** [`app/config/loader.py`](app/config/loader.py)

**Workaround:** Plan configuration changes during maintenance windows or use blue-green deployment with multiple instances.

### 6. Deduplication Scope Limited to Source

**Limitation:** `job_key` includes source type and identifier, meaning the same job posted across multiple ATS instances (e.g., company uses both Greenhouse and Lever) will be treated as distinct jobs.

**Impact:** Duplicate alerts if the same role is cross-posted to multiple ATSes.

**Module:** [`app/utils/hashing.py`](app/utils/hashing.py) (`compute_job_key`)

**Workaround:** Consolidate ATS sources or implement custom deduplication logic based on job titles/descriptions.

### 7. Network Dependency on Public APIs

**Limitation:** Adapters rely on public ATS APIs with no web scraping fallback.

**Impact:** Corporate firewalls, API outages, rate limiting, or API changes can break scans. No offline operation mode.

**Module:** [`app/adapters/`](app/adapters/) (all adapter implementations)

**Workaround:** Configure firewall rules to allow outbound HTTPS. Monitor adapter errors and implement retry logic for transient failures (already present in base adapter).

### 8. Location Filtering via Keywords Only

**Limitation:** No structured geolocation or geographic filtering. Remote vs on-site filtering must use keywords (e.g., "remote", "work-from-home") in `search_criteria`.

**Impact:** Imprecise location filtering; may miss jobs with non-standard location descriptions.

**Module:** [`app/matching/engine.py`](app/matching/engine.py)

**Workaround:** Expand keyword lists to cover variations (e.g., "remote", "work from home", "telecommute", "distributed").

### 9. Outbound Email Requirement

**Limitation:** Service cannot operate without working SMTP credentials. There is no offline queue or delayed notification mechanism.

**Impact:** SMTP outages prevent all notifications. No persistent retry queue beyond in-process attempts (max 3 retries with exponential backoff).

**Module:** [`app/notifications/service.py`](app/notifications/service.py), [`app/notifications/smtp_client.py`](app/notifications/smtp_client.py)

**Workaround:** Use reliable SMTP providers (SendGrid, SES, etc.). Monitor notification failure logs.

### 10. Single-Process Scheduler (No Horizontal Scaling)

**Limitation:** APScheduler runs in-process without clustering or distributed coordination. Scaling requires external orchestration.

**Impact:** Cannot horizontally scale by running multiple instances against the same workload. Each instance operates independently.

**Module:** [`app/scheduler/service.py`](app/scheduler/service.py)

**Workaround:** Shard workload by splitting sources across multiple instances with different configs. Use external schedulers (Kubernetes CronJobs, Airflow) to coordinate instances.

---

These limitations reflect v1.0 design choices prioritizing simplicity and reliability over enterprise scalability. Future versions may address these constraints based on user requirements.

## License & Contributing

### License

MIT License - See LICENSE file for details.

### Contributing

Contributions are welcome! Please follow these steps:

1. Review development setup in [Development & Testing](#development--testing)
2. Run tests before submitting: `pytest`
3. Follow code quality guidelines: `ruff check .`, `black .`, `mypy app`
4. Reference architecture docs: [`docs/job-opportunity-scanner-techspec.md`](docs/job-opportunity-scanner-techspec.md)

### Support

For issues and questions:
- Check [Troubleshooting](#troubleshooting) section
- Search existing GitHub issues
- Review logs with `--log-level DEBUG`
- Run `python verify_config.py` to validate configuration
- Test with sample harness: `python scripts/run_sample_scan.py`
