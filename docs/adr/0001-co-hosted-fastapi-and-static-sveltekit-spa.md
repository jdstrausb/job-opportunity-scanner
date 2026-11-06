---
status: accepted
---

# Co-Host FastAPI and Static SvelteKit 5 SPA in the Scanner Process

The scanner uses SQLite in WAL mode (`data/job_scanner.db`) and an in-memory `threading.Lock` (`ScanPipeline._lock`) to prevent overlapping scans and coordinate scheduler state. Running a separate frontend server or API container would split lock ownership across processes and introduce cross-container SQLite write contention and CORS configuration overhead. We therefore co-host FastAPI (`app/api/`) and the pre-built SvelteKit 5 static SPA (`frontend/build/` via `@sveltejs/adapter-static`) inside the single `job-scanner` Python process on port `8000` (`UI_PORT` / `--port`), managing `SchedulerService` inside FastAPI's lifespan context while preserving headless CLI execution for `--manual-run`, `--no-ui`, and standalone dismissal flags.

## Considered Options

- **Option A (Chosen)**: Single Python process running Uvicorn + FastAPI, managing `SchedulerService` via FastAPI lifespan, and serving the compiled SvelteKit static bundle with SPA fallback routing.
- **Option B**: Separate Node/SvelteKit server container and headless Python worker sharing a mounted SQLite volume. Rejected because `ScanPipeline._lock`, `last_run_result`, and hot-reloaded `AppConfig` live in memory and SQLite WAL discourages multi-container writer contention.
- **Option C**: Server-rendered Jinja2/HTMX views inside Python. Rejected because rich interactive client-side workflows (Search Profiles weight sliders, instant SerpAPI query builder/parser, reactive dismissal undo toasts) benefit from Svelte 5 runes state management.

## Consequences

- `Dockerfile` uses a multi-stage build (`node:22-slim` builder stage compiling `frontend/build`, copied into `/app/frontend/build` in the `python:3.13-slim` runtime image).
- CLI invocations with `--manual-run`, `--no-ui`, or `--dismiss`/`--undismiss`/`--list-dismissed` bypass Uvicorn entirely so scripts and cron jobs behave identically to v1.0.
