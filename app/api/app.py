"""FastAPI application factory for the co-hosted REST bridge (ADR-0001)."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config.exceptions import ConfigurationError
from app.config.manager import ConfigManager
from app.pipeline import ScanPipeline
from app.scheduler.service import SchedulerService

from .routes import config, dismissals, jobs, scan, sources

_DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1,0.0.0.0,testserver"


def create_app(
    pipeline: ScanPipeline,
    scheduler_service: Optional[SchedulerService] = None,
    config_manager: Optional[ConfigManager] = None,
    static_dir: Optional[Path] = None,
    manage_scheduler_lifespan: bool = False,
) -> FastAPI:
    """Build the API around already-constructed runtime services.

    Args:
        pipeline: The scan pipeline shared with the scheduler
        scheduler_service: Scheduler, used for ``next_run_time`` and (optionally) lifespan
        config_manager: Enables the config, source and registry routes
        static_dir: Built SPA directory to serve at ``/`` when it exists
        manage_scheduler_lifespan: Start/stop ``scheduler_service`` with the app
            (off by default so tests never spawn background schedulers)
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if manage_scheduler_lifespan and scheduler_service is not None:
            scheduler_service.start()
        try:
            yield
        finally:
            if manage_scheduler_lifespan and scheduler_service is not None:
                scheduler_service.shutdown(wait=False)

    app = FastAPI(title="Job Opportunity Scanner", lifespan=lifespan)
    app.state.pipeline = pipeline
    app.state.scheduler_service = scheduler_service
    app.state.config_manager = config_manager

    allowed_hosts = [
        h.strip()
        for h in (_DEFAULT_ALLOWED_HOSTS + "," + os.getenv("ALLOWED_HOSTS", "")).split(",")
        if h.strip()
    ]
    if "*" not in allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # One error shape for every HTTP error: {detail, errors}
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "errors": []},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(ConfigurationError)
    async def configuration_error_handler(request: Request, exc: ConfigurationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": exc.message,
                "errors": exc.errors,
                "suggestions": exc.suggestions,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # One error shape for every 422: {detail, errors}
        errors = [
            f"{' -> '.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content={"detail": "Request validation failed", "errors": errors}
        )

    for module in (jobs, dismissals, config, sources, scan):
        app.include_router(module.router, prefix="/api")

    _mount_spa(app, Path(static_dir) if static_dir is not None else None)
    return app


_HEALTH_PAYLOAD = {"status": "ok", "ui_built": False}


def _mount_spa(app: FastAPI, static_dir: Optional[Path]) -> None:
    """Serve the compiled SvelteKit bundle with an ``index.html`` fallback.

    Registered after the API routers, so ``/api/*`` always wins. Without a built
    bundle, ``/`` answers with a small health payload and everything else 404s.
    """
    if static_dir is None or not static_dir.is_dir():

        @app.get("/", include_in_schema=False)
        def health() -> dict[str, object]:
            return _HEALTH_PAYLOAD

        return

    root = static_dir.resolve()
    index = root / "index.html"
    assets = root / "_app"
    if assets.is_dir():
        # A missing hashed asset must 404, never fall through to the HTML shell
        app.mount("/_app", StaticFiles(directory=str(assets)), name="spa-assets")

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa_fallback(full_path: str) -> Union[FileResponse, dict[str, object]]:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")

        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)

        if index.is_file():
            return FileResponse(index)
        return _HEALTH_PAYLOAD
