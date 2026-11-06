"""Comment-preserving config editing with lock-synchronized runtime hot-reload.

``ConfigManager`` patches ``config.yaml`` in place using ``ruamel.yaml`` round-trip
mode, so user comments and unexpanded ``identifier: "*"`` Wildcard Sources survive
every save. A candidate document is validated with ``AppConfig`` before anything is
written, the file is swapped atomically, and the attached pipeline, keyword matcher
and scheduler are updated while holding ``ScanPipeline._lock``.
"""

import io
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap

from app.utils.atomic_write import atomic_write_text

from .exceptions import ConfigurationError
from .models import (
    AdvancedConfig,
    AppConfig,
    EmailConfig,
    FilteringRules,
    LoggingConfig,
    SearchProfile,
    SourceConfig,
)

logger = logging.getLogger(__name__)

_SECTION_FIELDS = (
    "search_profiles",
    "filtering",
    "sources",
    "scan_interval",
    "email",
    "logging",
    "advanced",
)


class ConfigUpdatePayload(BaseModel):
    """Sections to change; ``None`` leaves a section untouched.

    Inside a mapping section, keys absent from the payload are kept.
    """

    search_profiles: Optional[List[SearchProfile]] = None
    filtering: Optional[FilteringRules] = None
    sources: Optional[List[SourceConfig]] = None
    scan_interval: Optional[str] = None
    email: Optional[EmailConfig] = None
    logging: Optional[LoggingConfig] = None
    advanced: Optional[AdvancedConfig] = None


@dataclass
class ConfigReloadResult:
    """Outcome of a successful ``ConfigManager.update_config()``."""

    raw_config: AppConfig
    expanded_config: AppConfig
    reevaluated_jobs_count: int = 0


def _to_plain(value: Any) -> Any:
    """Convert ruamel containers and scalar subclasses to plain Python values."""
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


def _merge(target: Any, new: Any) -> Any:
    """Like ``_patch`` but keys absent from ``new`` are kept (key-level deep merge)."""
    if isinstance(target, CommentedMap) and isinstance(new, dict):
        for key, value in new.items():
            target[key] = _merge(target[key], value) if key in target else value
        return target
    return new


def _patch(target: Any, new: Any) -> Any:
    """Return ``target`` updated to equal ``new``, keeping comments where possible.

    Mappings are patched key by key (untouched keys keep their comments, absent keys
    are removed); everything else, lists included, is replaced wholesale.
    """
    if isinstance(target, CommentedMap) and isinstance(new, dict):
        for key in [k for k in target if k not in new]:
            del target[key]
        for key, value in new.items():
            target[key] = _patch(target[key], value) if key in target else value
        return target
    return new


class ConfigManager:
    """Reads, edits and hot-reloads the application config file."""

    def __init__(
        self,
        config_path: Path,
        *,
        pipeline: Optional[Any] = None,
        scheduler_service: Optional[Any] = None,
        registry_dir: Path = Path("data/registries"),
        lock_timeout: float = 30.0,
    ) -> None:
        """
        Args:
            config_path: Path to ``config.yaml``
            pipeline: ``ScanPipeline`` to hot-reload (optional)
            scheduler_service: ``SchedulerService`` to reschedule (optional)
            registry_dir: Directory holding the company registry JSON files
            lock_timeout: Seconds to wait for an in-flight scan before giving up
        """
        self.config_path = Path(config_path)
        self.pipeline = pipeline
        self.scheduler_service = scheduler_service
        self.registry_dir = Path(registry_dir)
        self.lock_timeout = lock_timeout
        self._write_lock = threading.RLock()

    # -- reading ---------------------------------------------------------

    def _yaml(self) -> YAML:
        yaml = YAML(typ="rt")
        yaml.preserve_quotes = True
        yaml.width = 4096  # never re-wrap long strings
        return yaml

    def _load_document(self) -> CommentedMap:
        try:
            with open(self.config_path, "r") as f:
                document = self._yaml().load(f)
        except FileNotFoundError as e:
            raise ConfigurationError(
                f"Configuration file not found: {self.config_path}",
                suggestions=["Copy config.example.yaml to config.yaml"],
            ) from e
        except (YAMLError, OSError) as e:
            raise ConfigurationError(f"Failed to read configuration file: {e}") from e
        if not isinstance(document, CommentedMap):
            raise ConfigurationError("Configuration file must contain a YAML mapping")
        return document

    @staticmethod
    def _validate(document: Any) -> AppConfig:
        try:
            return AppConfig.model_validate(_to_plain(document))
        except ValidationError as e:
            errors = [
                f"{' -> '.join(str(part) for part in err['loc'])}: {err['msg']}"
                for err in e.errors()
            ]
            raise ConfigurationError(
                "Configuration validation failed",
                errors=errors,
                suggestions=["Review config.example.yaml for the expected format"],
            ) from e

    def get_raw_config(self) -> AppConfig:
        """Return the config as written on disk (Wildcard Sources unexpanded)."""
        with self._write_lock:
            return self._validate(self._load_document())

    def get_expanded_config(self) -> AppConfig:
        """Return the config with Wildcard Sources expanded from the registries."""
        return self._expand(self.get_raw_config())

    def _expand(self, raw: AppConfig) -> AppConfig:
        from app.adapters.registry import CompanyRegistry, expand_sources_with_registry

        registry = CompanyRegistry(self.registry_dir)
        registry.load()
        return raw.model_copy(
            update={"sources": expand_sources_with_registry(list(raw.sources), registry)},
            deep=True,
        )

    # -- writing ---------------------------------------------------------

    def update_config(self, payload: ConfigUpdatePayload) -> ConfigReloadResult:
        """Apply a payload to ``config.yaml`` and hot-reload the runtime.

        Order: patch the round-trip document, validate the candidate, wait for any
        in-flight scan (``ScanPipeline._lock``), write atomically, then swap runtime
        state. Any failure before the write leaves disk and runtime untouched.

        Raises:
            ConfigurationError: If the candidate is invalid, the file cannot be
                read or written, or a running scan did not finish within
                ``lock_timeout``
        """
        with self._write_lock:
            document = self._load_document()
            previous = self.get_raw_config()
            self._apply_payload(document, payload)
            candidate = self._validate(document)

            lock = getattr(self.pipeline, "_lock", None)
            if lock is not None and not lock.acquire(timeout=self.lock_timeout):
                raise ConfigurationError(
                    "A scan is still running; configuration was not changed",
                    suggestions=["Wait for the running scan to finish and save again"],
                )
            try:
                self._write_atomically(document)
                expanded = self._expand(candidate)
                reevaluated = self._hot_reload(
                    expanded,
                    reevaluate=payload.search_profiles is not None,
                    interval_changed=candidate.scan_interval_seconds
                    != previous.scan_interval_seconds,
                )
            finally:
                if lock is not None:
                    lock.release()

            return ConfigReloadResult(
                raw_config=candidate.model_copy(deep=True),
                expanded_config=expanded,
                reevaluated_jobs_count=reevaluated,
            )

    def reload_registries(self) -> AppConfig:
        """Re-expand Wildcard Sources from the registries and update the pipeline."""
        with self._write_lock:
            expanded = self.get_expanded_config()
            lock = getattr(self.pipeline, "_lock", None)
            if lock is not None and not lock.acquire(timeout=self.lock_timeout):
                raise ConfigurationError("A scan is still running; registries were not reloaded")
            try:
                if self.pipeline is not None:
                    self.pipeline.app_config = expanded
            finally:
                if lock is not None:
                    lock.release()
            return expanded

    @staticmethod
    def _apply_payload(document: CommentedMap, payload: ConfigUpdatePayload) -> None:
        for name in _SECTION_FIELDS:
            value = getattr(payload, name)
            if value is None:
                continue
            if isinstance(value, list):
                plain: Any = [
                    item.model_dump(mode="json", exclude_unset=True) for item in value
                ]
            elif isinstance(value, BaseModel):
                plain = value.model_dump(mode="json", exclude_unset=True)
            else:
                plain = value

            if name == "search_profiles" and "search_criteria" in document:
                # Migrate the legacy single-track key in place
                position = list(document.keys()).index("search_criteria")
                if "search_profiles" in document:
                    document["search_profiles"] = plain
                else:
                    document.insert(position, "search_profiles", plain)
                del document["search_criteria"]
            elif name in document:
                document[name] = (
                    _merge(document[name], plain)
                    if isinstance(plain, dict)
                    else _patch(document[name], plain)
                )
            else:
                document[name] = plain

    def _write_atomically(self, document: CommentedMap) -> None:
        try:
            buffer = io.StringIO()
            self._yaml().dump(document, buffer)
            atomic_write_text(self.config_path, buffer.getvalue())
        except OSError as e:
            raise ConfigurationError(f"Failed to write configuration file: {e}") from e

    def _hot_reload(self, expanded: AppConfig, *, reevaluate: bool, interval_changed: bool) -> int:
        """Swap runtime state. Caller holds ``pipeline._lock`` when a pipeline is attached."""
        if self.pipeline is None:
            return 0

        from app.matching.engine import KeywordMatcher

        self.pipeline.app_config = expanded
        self.pipeline.keyword_matcher = KeywordMatcher(expanded.get_effective_profiles())

        if self.scheduler_service is not None and interval_changed:
            assert expanded.scan_interval_seconds is not None
            self.scheduler_service.reschedule(expanded.scan_interval_seconds)

        if not reevaluate:
            return 0

        from app.persistence.database import get_session
        from app.persistence.repositories import reevaluate_stored_jobs

        with get_session() as session:
            count = reevaluate_stored_jobs(
                session, self.pipeline.keyword_matcher, only_unscored=False
            )
        logger.info(
            "Re-evaluated %d stored jobs after config change",
            count,
            extra={"event": "config.reevaluated", "count": count},
        )
        return count
