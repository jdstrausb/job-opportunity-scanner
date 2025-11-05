# Dockerization Technical Design

## Purpose & Scope
- Package the Job Opportunity Scanner into a reproducible Docker image aligned with Implementation Guide step 10.
- Ensure the container preserves SQLite data and exposes configuration via environment variables required by `EnvironmentConfig` (`app/config/environment.py:37`).
- Deliver actionable implementation guidance without embedding literal source code.

## Current Application Context
- Primary service entrypoint is the console script `job-scanner` defined in `pyproject.toml` and implemented in `app/main.py:78`, which orchestrates configuration loading, database setup, and scheduler startup.
- Runtime configuration depends on a YAML file (`config.yaml`) read relative to the working directory unless overridden via the `--config` CLI flag (`app/main.py:90`).
- Default database URL resolves to `sqlite:///./data/job_scanner.db` via environment parsing (`app/config/environment.py:34`), so the container must provide a writable `data/` directory.
- Required environment variables for notifications (`SMTP_HOST`, `SMTP_PORT`, `ALERT_TO_EMAIL`, etc.) are validated during startup (`app/config/environment.py:37-147`).

## Functional Objectives
- Provide a Dockerfile that installs Python 3.13 runtime dependencies, copies the application, and configures an executable entrypoint.
- Guarantee the image can be built locally (`docker build`) and launched in both scheduler (default) and `--manual-run` modes.
- Document persistent storage expectations (SQLite volume mount) and all environment variables the container must receive.

## Assumptions & Constraints
- Build uses `python:3.13-slim` (or equivalent Debian-based image) to satisfy `requires-python >= 3.13` in `pyproject.toml`.
- Deployment environment supplies Docker engine 20.10+ with BuildKit enabled.
- No native extensions are required by dependencies, so system packages beyond `curl`, `ca-certificates`, and `gcc` are unnecessary.
- Container runtime does not provide secrets management; credentials arrive via environment variables or `.env` files mounted at run time.
- Application runs entirely in a single container; horizontal scaling or orchestration (Kubernetes, ECS) is out of scope for this step.

## Docker Image Design
- **Base Image**: Start from `python:3.13-slim` to minimize footprint while bundling required OS libraries.
- **Working Directory**: Set to `/app` so default relative paths (`config.yaml`, `./data`) remain valid without additional overrides.
- **Dependency Installation**: Copy `pyproject.toml` and lock files first, perform `pip install --no-cache-dir .` using the project’s build backend, then copy the remainder of the application to leverage Docker layer caching.
- **Virtual Environment Strategy**: Use the system interpreter with `pip install --no-cache-dir --disable-pip-version-check` to reduce image size; rely on the project’s console script entry point being installed onto `PATH`.
- **Non-root Execution**: Create a dedicated user (e.g., `scanner`) and group, ensure `/app`, `/app/data`, and `/app/.cache` directories are owned by that user to mitigate security risks.
- **Configuration Artifacts**: Copy `config.example.yaml` into the image as documentation (`/app/config/example.config.yaml`), but expect operators to mount a real `config.yaml`.
- **Environment Defaults**: Set `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, and `ENVIRONMENT=production` (overridable). Do not hardcode credentials; surface placeholders in documentation.
- **Entrypoint & Command**: Configure the container to run the console script (`job-scanner`) by default. Allow overriding with `--manual-run` or alternate CLI flags via `docker run` arguments.
- **Health Considerations**: Expose readiness via process exit status—no HTTP probes are required. Document that schedulers should monitor container logs for `service.starting` events (`app/main.py:120`).

## Data Persistence & Volumes
- Default SQLite database path resolves to `/app/data/job_scanner.db`, so the Dockerfile must create `/app/data` and the runtime instructions must bind-mount host storage to preserve job history between restarts.
- Recommend a named volume (e.g., `job_scanner_data`) or bind mount: `docker run -v job_scanner_data:/app/data ...` (or `-v $(pwd)/data:/app/data` for local testing).
- Document optional override using `DATABASE_URL` for external databases; ensure instructions call out that connection strings must be compatible with SQLAlchemy initialisation (`app/persistence/database.py:39-121`).

## Environment Variable Requirements
- Required: `SMTP_HOST`, `SMTP_PORT`, `ALERT_TO_EMAIL`.
- Optional (commonly used): `SMTP_USER`, `SMTP_PASS`, `SMTP_SENDER_NAME`, `LOG_LEVEL`, `ENVIRONMENT`, `DATABASE_URL`.
- Highlight how to provide them securely:
  - `.env` file mounted via `--env-file`.
  - Explicit `-e KEY=value` flags.
  - Secrets managers (documented as future enhancement if relevant).
- Emphasise that missing values halt startup with `ConfigurationError` (`app/config/environment.py:73-136`).

## Step-by-Step Implementation Guide
1. **Create `.dockerignore`**: Exclude paths that inflate the build context (e.g., `.git`, `.venv`, `htmlcov/`, `tests/`, `docs/`, `data/`, `*.pyc`, `__pycache__/`, `.pytest_cache/`) to improve build speed and protect secrets.
2. **Author the Dockerfile**:
   - Declare build arguments for versioning (e.g., `APP_VERSION` or `BUILD_DATE`) if desired for traceability.
   - Install OS packages (`apt-get update && apt-get install --no-install-recommends`) limited to `curl` and `ca-certificates`; remove cache afterward (`rm -rf /var/lib/apt/lists/*`).
   - Copy dependency manifests (`pyproject.toml`, `uv.lock`) and install project dependencies with `pip install --no-cache-dir .`.
   - Copy application source (`app/`, `config.example.yaml`, `verify_config.py`, `README.md`) into `/app`.
   - Ensure `/app/data` exists and adjust ownership to the non-root user.
   - Set environment variables (`PYTHONUNBUFFERED`, etc.) and `PATH` updates if the console script is installed under `/home/scanner/.local/bin`.
   - Switch to the non-root user and set `ENTRYPOINT` to the console script.
3. **Document Runtime Configuration**:
   - Capture sample commands for both scheduler mode and manual run:
     - Scheduler: `docker run --rm -v job_scanner_data:/app/data --env-file path/to/.env -v $(pwd)/config.yaml:/app/config.yaml job-opportunity-scanner:latest`.
     - Manual run: append `-- --manual-run` to the command.
   - Clarify that the default configuration file should be mounted to `/app/config.yaml`; without it, the service will read the baked-in example (and likely fail validation).
4. **Build Validation**:
   - Run `docker build -t job-opportunity-scanner:latest .` locally; include BuildKit-enabled instructions if necessary (`DOCKER_BUILDKIT=1`).
   - Execute a manual scan container with mocked SMTP credentials and inspect logs for `service.manual_scan.completed` (`app/main.py:175`).
   - Verify that stopping the container retains `data/job_scanner.db` via the mounted volume.
5. **Operational Documentation**:
   - Update `README.md` (or create a new documentation section) summarising environment variables, volume mounts, and sample commands from steps above.
   - Note log visibility (stdout/stderr), recommended resource limits, and how to inspect the SQLite database using `docker run --rm -it -v job_scanner_data:/app/data python:3.13-slim sqlite3`.
6. **Optional Enhancements (Future)**:
   - Outline potential CI automation (GitHub Actions) to build and push images on release.
   - Record metrics for image size or build duration to benchmark over time.

## Testing & Verification Strategy
- Validate the container by executing `job-scanner --manual-run` in the image with a stub SMTP configuration (e.g., `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587` but with `SMTP_USER/PASS` omitted to trigger authentication warnings).
- Run automated tests (`pytest`) from a derived stage to ensure compatibility; alternatively, document running `pytest` on the host before building.
- Confirm that the scheduler loop runs by launching the container without `--manual-run`, waiting beyond one interval defined in `config.yaml` (default 15 minutes) or temporarily overriding `scan_interval` for faster verification.
- Inspect file ownership within `/app/data` after container start to ensure the non-root user can write to the SQLite database.

## Risks & Mitigations
- **Missing Environment Variables**: Startup failures mitigated by clear documentation and `.env` template updates.
- **File Permission Issues**: Mitigated by pre-setting ownership for `/app/data` and instructing users to mount volumes with compatible permissions.
- **Container Size Bloat**: Use slim base image, purge apt caches, and rely on `.dockerignore` to keep the context lean.
- **SQLite Locking**: Document that concurrent containers sharing the same volume are unsupported; recommend a single writer or migrating to a managed database via `DATABASE_URL`.

## Follow-Up Questions / Assumptions
- Is image publishing to a registry (e.g., GHCR or Docker Hub) required for MVP? Current design assumes local or manually pushed images.
- Should we embed health checks or rely on orchestrator-level monitoring? For now, assume log monitoring is sufficient.
- Will there be a need for configuration templates for different environments (staging vs production)? If yes, consider adding a dedicated `config/` folder in future iterations.

