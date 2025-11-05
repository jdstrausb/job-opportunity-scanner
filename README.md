# Job Opportunity Scanner

An automated service that monitors Applicant Tracking System (ATS) APIs for configured companies, evaluates job postings against keyword rules, deduplicates results, and sends targeted email notifications for matching opportunities.

## Features

- **Multi-ATS Support**: Integrates with Greenhouse, Lever, and Ashby job posting systems
- **Keyword-Based Filtering**: Define required terms, OR groups, and exclusion lists for precise job matching
- **Deduplication**: Intelligent change detection prevents duplicate notifications
- **Email Notifications**: Receive alerts for matching job postings with matched keyword highlights
- **Scheduled Scanning**: Configurable polling interval (default: 15 minutes)
- **SQLite Persistence**: Local database for job history and alert tracking
- **Docker Ready**: Containerized deployment with environment-based configuration
- **Observability**: Structured logging for monitoring and troubleshooting

## Quick Start

### Prerequisites

- Python 3.13 or higher
- Docker (optional, for containerized deployment)
- SMTP-compatible email provider (Gmail, SendGrid, corporate mail server, etc.)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/job-opportunity-scanner.git
cd job-opportunity-scanner
```

2. Install dependencies using `uv`:
```bash
uv sync
```

Or with pip:
```bash
pip install -e .
```

### Configuration

1. Copy the example configuration:
```bash
cp config.example.yaml config.yaml
```

2. Edit `config.yaml` with your ATS sources and keyword rules:
```yaml
sources:
  - name: "MyCompany Careers"
    type: "greenhouse"
    identifier: "mycompany"
    enabled: true

scan_interval: "15m"

search_criteria:
  required_terms:
    - "python"
    - "backend"
  keyword_groups:
    - ["remote", "work-from-home"]
  exclude_terms:
    - "django"
    - "legacy"
```

3. Set environment variables for email configuration:
```bash
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="your-email@gmail.com"
export SMTP_PASS="your-app-password"
export ALERT_TO_EMAIL="alerts@example.com"
```

Or copy and update `.env.example`:
```bash
cp .env.example .env
# Edit .env with your credentials
source .env
```

### Running the Service

**Manual scan (test mode):**
```bash
python -m app.main --manual-run
```

**Run as daemon with scheduler:**
```bash
python -m app.main
```

**With custom config:**
```bash
python -m app.main --config /path/to/config.yaml
```

**With debug logging:**
```bash
python -m app.main --log-level DEBUG
```

### Docker Deployment

#### Building the Image

Build the Docker image locally:
```bash
docker build -t job-opportunity-scanner:latest .
```

Optional: Build with BuildKit for improved caching:
```bash
DOCKER_BUILDKIT=1 docker build -t job-opportunity-scanner:latest .
```

Build with custom version and build date:
```bash
docker build \
  --build-arg APP_VERSION=1.0.0 \
  --build-arg BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ') \
  -t job-opportunity-scanner:latest .
```

#### Running the Container

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

**With bind mount for local testing:**
```bash
docker run -d \
  --name job-scanner \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/config.yaml:/app/config.yaml \
  --env-file .env \
  job-opportunity-scanner:latest
```

#### Required Environment Variables

The following environment variables **must** be set for the container to start:

- `SMTP_HOST` - SMTP server hostname (e.g., `smtp.gmail.com`)
- `SMTP_PORT` - SMTP server port (e.g., `587` for TLS, `465` for SSL)
- `ALERT_TO_EMAIL` - Email address to receive job alerts

#### Optional Environment Variables

- `SMTP_USER` - SMTP authentication username (if required by your server)
- `SMTP_PASS` - SMTP authentication password (if required by your server)
- `SMTP_SENDER_NAME` - Display name for email sender (default: "Job Opportunity Scanner")
- `LOG_LEVEL` - Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL (default: INFO)
- `ENVIRONMENT` - Environment name: development, staging, production (default: production)
- `DATABASE_URL` - SQLAlchemy database URL (default: `sqlite:///./data/job_scanner.db`)

Missing required variables will cause startup to fail with a `ConfigurationError`.

#### Data Persistence

The container stores SQLite database at `/app/data/job_scanner.db`. To preserve job history between container restarts, mount a volume:

**Named volume (recommended for production):**
```bash
docker volume create job_scanner_data
docker run -v job_scanner_data:/app/data ...
```

**Bind mount (useful for development/inspection):**
```bash
docker run -v $(pwd)/data:/app/data ...
```

⚠️ **Important**: Only run one container instance per volume. SQLite does not support concurrent writes from multiple containers.

#### Configuration File

Mount your `config.yaml` to `/app/config.yaml`:
```bash
-v $(pwd)/config.yaml:/app/config.yaml
```

Without this mount, the container will attempt to use a default configuration and will likely fail validation.

#### Inspecting the Database

To inspect the SQLite database using the database volume:
```bash
docker run --rm -it \
  -v job_scanner_data:/app/data \
  python:3.13-slim \
  sqlite3 /app/data/job_scanner.db
```

Inside the SQLite shell:
```sql
.tables
SELECT * FROM jobs LIMIT 10;
.quit
```

#### Container Logs

View container logs to monitor scan progress:
```bash
# Follow logs in real-time
docker logs -f job-scanner

# View last 100 lines
docker logs --tail 100 job-scanner

# View logs with timestamps
docker logs -t job-scanner
```

Look for these log events:
- `service.starting` - Service initialization
- `service.manual_scan.completed` - Manual scan finished
- `pipeline.scan.completed` - Scheduled scan finished
- Errors and warnings for troubleshooting

#### Resource Limits

Recommended resource limits for production:
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

#### Stopping and Removing

```bash
# Stop the container
docker stop job-scanner

# Remove the container
docker rm job-scanner

# Stop and remove in one command
docker rm -f job-scanner
```

The data volume persists even after container removal. To remove the volume:
```bash
docker volume rm job_scanner_data
```

## Configuration Reference

See [docs/job-opportunity-scanner-techspec.md](docs/job-opportunity-scanner-techspec.md) for detailed configuration and architecture documentation.

### Configuration Schema

- **sources**: List of ATS sources to monitor
  - `name`: Display name for the source
  - `type`: ATS type (`greenhouse`, `lever`, `ashby`)
  - `identifier`: Company identifier or slug specific to the ATS
  - `enabled`: Optional flag to enable/disable source (default: `true`)

- **scan_interval**: Duration string for polling frequency
  - Examples: `5m`, `15m`, `1h`, `2d`
  - Minimum: 5 minutes

- **search_criteria**: Keyword matching rules
  - `required_terms`: All terms must match (AND logic)
  - `keyword_groups`: At least one term per group must match (OR within groups)
  - `exclude_terms`: Exclude postings containing any of these terms

## Development

### Install development dependencies:
```bash
pip install -e ".[dev]"
```

### Run tests:
```bash
pytest
pytest --cov=app  # with coverage
```

### Code quality:
```bash
ruff check .
black --check .
mypy app
```

## Project Structure

```
job-opportunity-scanner/
├── app/                      # Main application package
│   ├── config/              # Configuration management
│   ├── adapters/            # ATS-specific adapters
│   ├── normalization/       # Data normalization layer
│   ├── persistence/         # Database operations
│   ├── matching/            # Keyword matching engine
│   ├── scheduler/           # Task scheduling
│   ├── notifications/       # Email notification service
│   ├── logging/             # Logging configuration
│   ├── utils/               # Utility functions
│   └── main.py              # Application entry point
├── tests/                    # Test suite
├── data/                     # SQLite database storage
├── docs/                     # Documentation
├── config.example.yaml       # Example configuration
├── .env.example              # Example environment variables
├── .gitignore                # Git ignore rules
├── pyproject.toml            # Project configuration and dependencies
└── README.md                 # This file
```

## Architecture

The service follows a modular pipeline architecture:

1. **Configuration Loader** → reads and validates YAML configuration
2. **Scheduler Service** → orchestrates periodic execution
3. **ATS Adapters** → fetch job postings from various platforms
4. **Normalization Layer** → convert to unified domain model
5. **Persistence Layer** → SQLite storage and change detection
6. **Matching Engine** → apply keyword rules
7. **Notification Service** → send email alerts
8. **Logging** → structured event emission for observability

For detailed architecture documentation, see [docs/job-opportunity-scanner-techspec.md](docs/job-opportunity-scanner-techspec.md).

## Testing

The project includes comprehensive unit and integration tests:

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=app --cov-report=html

# Run specific test module
pytest tests/test_matching.py

# Run tests matching a pattern
pytest -k "test_greenhouse"
```

Test fixtures include recorded API responses for each ATS platform to enable offline testing.

## Troubleshooting

### Email Not Sending

- Check SMTP credentials and environment variables
- Verify TLS is enabled on your SMTP server
- Check logs for authentication errors: `--log-level DEBUG`
- Some email providers (Gmail) require app-specific passwords

### No Jobs Found

- Verify company identifiers in `config.yaml` are correct for each ATS
- Check if company's careers page is accessible from your network
- Review logs for adapter errors and HTTP status codes

### Database Lock

- Ensure only one instance is running at a time
- Check for stale processes: `ps aux | grep job-scanner`
- Delete `.db-journal` file if present and restart

## License

MIT License - See LICENSE file for details

## Contributing

Contributions are welcome! Please see the development guide in the documentation for setup and testing procedures.

## Support

For issues and questions:
- Check existing GitHub issues
- Review logs with `--log-level DEBUG`
- See troubleshooting section above
