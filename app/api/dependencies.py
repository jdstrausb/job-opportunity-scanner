"""FastAPI dependency providers for runtime services held on ``app.state``."""

from pathlib import Path
from typing import Generator, Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.config.manager import ConfigManager
from app.persistence.database import get_session
from app.pipeline import ScanPipeline
from app.scheduler.service import SchedulerService

DEFAULT_REGISTRY_DIR = Path("data/registries")


def database_session() -> Generator[Session, None, None]:
    """Yield a transactional session (commits on success, rolls back on error)."""
    with get_session() as session:
        yield session


def get_pipeline(request: Request) -> ScanPipeline:
    pipeline: ScanPipeline = request.app.state.pipeline
    return pipeline


def get_scheduler(request: Request) -> Optional[SchedulerService]:
    scheduler: Optional[SchedulerService] = request.app.state.scheduler_service
    return scheduler


def get_optional_config_manager(request: Request) -> Optional[ConfigManager]:
    manager: Optional[ConfigManager] = request.app.state.config_manager
    return manager


def get_config_manager(request: Request) -> ConfigManager:
    """Return the ConfigManager, or 503 when the app was built without one."""
    manager: Optional[ConfigManager] = request.app.state.config_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="Configuration management is not available")
    return manager


def get_registry_dir(request: Request) -> Path:
    manager: Optional[ConfigManager] = request.app.state.config_manager
    return manager.registry_dir if manager is not None else DEFAULT_REGISTRY_DIR
