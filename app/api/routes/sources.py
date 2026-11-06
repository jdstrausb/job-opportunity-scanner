"""Source health rollups, company registries, and the SerpAPI live query test."""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.adapters.base import BaseAdapter
from app.adapters.exceptions import AdapterError
from app.adapters.registry import CompanyRegistry
from app.adapters.serpapi_google import SerpApiSearchAdapter
from app.api.dependencies import (
    database_session,
    get_config_manager,
    get_optional_config_manager,
    get_pipeline,
    get_registry_dir,
)
from app.api.schemas import (
    ATSRegistryType,
    CompanyHealth,
    ExplicitSourceHealth,
    HealthStatus,
    RawJobPreview,
    RegistryCompany,
    RegistryReplaceRequest,
    RegistrySummary,
    SourcesResponse,
    TestQueryRequest,
    TestQueryResponse,
    WildcardRollupHealth,
)
from app.config.exceptions import ConfigurationError
from app.config.manager import ConfigManager
from app.config.models import SourceConfig
from app.domain.models import SourceStatus
from app.persistence.repositories import SourceRepository
from app.pipeline import ScanPipeline

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sources"])

_REGISTRY_TYPES = ("greenhouse", "lever", "ashby")


def _safe(text: Optional[str]) -> Optional[str]:
    return BaseAdapter._sanitize_url(text) if text else None


def _health(enabled: bool, status: Optional[SourceStatus]) -> HealthStatus:
    if not enabled:
        return "disabled"
    if status is None:
        return "unscanned"
    if status.last_error_at is not None:
        return "error"
    return "healthy" if status.last_success_at is not None else "unscanned"


def _latest(values: List[Optional[datetime]]) -> Optional[datetime]:
    present = [v for v in values if v is not None]
    return max(present) if present else None


@router.get("/sources", response_model=SourcesResponse)
def get_sources(
    manager: ConfigManager = Depends(get_config_manager),
    registry_dir: Path = Depends(get_registry_dir),
    session: Session = Depends(database_session),
) -> SourcesResponse:
    statuses: Dict[Tuple[str, str], SourceStatus] = {
        (s.source_type, s.source_identifier): s for s in SourceRepository(session).get_all()
    }
    registry = CompanyRegistry(registry_dir)
    explicit: List[ExplicitSourceHealth] = []
    rollups: List[WildcardRollupHealth] = []

    for source in manager.get_raw_config().sources:
        if source.identifier != "*":
            status = statuses.get((str(source.type), source.identifier))
            explicit.append(
                ExplicitSourceHealth(
                    name=source.name,
                    type=str(source.type),
                    identifier=source.identifier,
                    enabled=source.enabled,
                    health_status=_health(source.enabled, status),
                    last_success_at=status.last_success_at if status else None,
                    last_error_at=status.last_error_at if status else None,
                    error_message=_safe(status.error_message) if status else None,
                )
            )
            continue

        companies: List[CompanyHealth] = []
        ats = str(source.type)
        raw_companies = registry.get_companies(ats, enabled_only=False) if ats in _REGISTRY_TYPES else []
        for company in raw_companies:
            enabled = bool(company.get("enabled", True)) and source.enabled
            status = statuses.get((ats, company["identifier"]))
            companies.append(
                CompanyHealth(
                    identifier=company["identifier"],
                    name=company["name"],
                    enabled=bool(company.get("enabled", True)),
                    health_status=_health(enabled, status),
                    last_success_at=status.last_success_at if status else None,
                    last_error_at=status.last_error_at if status else None,
                    error_message=_safe(status.error_message) if status else None,
                )
            )
        companies.sort(key=lambda c: (c.health_status != "error", c.name.lower()))
        counted = [c for c in companies if c.health_status != "disabled"]
        rollups.append(
            WildcardRollupHealth(
                name=source.name,
                type=ats,
                enabled=source.enabled,
                total_companies=len(companies),
                total_enabled=len(counted),
                healthy_count=sum(c.health_status == "healthy" for c in counted),
                error_count=sum(c.health_status == "error" for c in counted),
                unscanned_count=sum(c.health_status == "unscanned" for c in counted),
                last_success_at=_latest([c.last_success_at for c in companies]),
                last_error_at=_latest([c.last_error_at for c in companies]),
                companies=companies,
            )
        )
    return SourcesResponse(explicit_sources=explicit, wildcard_rollups=rollups)


@router.post("/sources/test-query", response_model=TestQueryResponse)
def test_query(body: TestQueryRequest, pipeline: ScanPipeline = Depends(get_pipeline)):
    """Run one SerpAPI query without persisting or alerting."""
    env = pipeline.env_config
    if not env.serpapi_api_key:
        return JSONResponse(
            status_code=400,
            content={
                "detail": "SERPAPI_API_KEY is not configured in the environment",
                "errors": [],
                "serpapi_configured": False,
            },
        )

    advanced = pipeline.app_config.advanced
    adapter = SerpApiSearchAdapter(
        timeout=advanced.http_request_timeout,
        user_agent=advanced.user_agent,
        api_key=env.serpapi_api_key,
    )
    source = SourceConfig(
        name="Query test",
        type="serpapi_google",  # type: ignore[arg-type]
        identifier=body.query,
        enabled=True,
        options={"num_results": body.num_results},
    )
    try:
        jobs = adapter.fetch_jobs(source)
    except AdapterError as e:
        message = str(_safe(str(e)))
        if env.serpapi_api_key:
            message = message.replace(env.serpapi_api_key, "REDACTED")
        logger.warning("SerpAPI test query failed: %s", message)
        raise HTTPException(status_code=502, detail=message) from None

    previews = [
        RawJobPreview(
            title=j.title,
            company=j.company,
            location=j.location,
            url=j.url,
            is_remote=j.is_remote,
            # Un-hydrated results fall back to an id derived from the link
            hydrated=j.external_id != hashlib.sha256(j.url.encode("utf-8")).hexdigest(),
        )
        for j in jobs
    ]
    return TestQueryResponse(query=body.query, count=len(previews), jobs=previews)


# -- registries ---------------------------------------------------------------


def _summary(
    registry: CompanyRegistry,
    ats_type: str,
    *,
    q: Optional[str] = None,
    enabled_only: bool = False,
    reloaded: bool = True,
) -> RegistrySummary:
    meta = registry.get_registry_metadata(ats_type)
    all_companies = registry._read_document(ats_type)["companies"]
    visible = all_companies
    if enabled_only:
        visible = [c for c in visible if c.get("enabled", True)]
    if q and q.strip():
        needle = q.strip().lower()
        visible = [
            c for c in visible if needle in c["identifier"].lower() or needle in c["name"].lower()
        ]
    return RegistrySummary(
        ats_type=ats_type,
        version=str(meta["version"]),
        updated_at=meta["updated_at"],
        total=len(all_companies),
        enabled_count=sum(1 for c in all_companies if c.get("enabled", True)),
        companies=[
            RegistryCompany(
                identifier=c["identifier"], name=c["name"], enabled=bool(c.get("enabled", True))
            )
            for c in visible
        ],
        reloaded=reloaded,
    )


def _reload(manager: Optional[ConfigManager]) -> bool:
    """Re-expand Wildcard Sources; False if a running scan blocked it."""
    if manager is None:
        return True
    try:
        manager.reload_registries()
    except ConfigurationError as e:
        logger.warning("Registry saved but wildcard reload deferred: %s", e.message)
        return False
    return True


@router.get("/registries/{ats_type}", response_model=RegistrySummary)
def get_registry(
    ats_type: ATSRegistryType,
    q: Optional[str] = None,
    enabled_only: bool = False,
    registry_dir: Path = Depends(get_registry_dir),
) -> RegistrySummary:
    return _summary(CompanyRegistry(registry_dir), ats_type, q=q, enabled_only=enabled_only)


@router.put("/registries/{ats_type}", response_model=RegistrySummary)
def put_registry(
    ats_type: ATSRegistryType,
    body: RegistryReplaceRequest,
    registry_dir: Path = Depends(get_registry_dir),
    manager: Optional[ConfigManager] = Depends(get_optional_config_manager),
) -> RegistrySummary:
    registry = CompanyRegistry(registry_dir)
    if body.companies is not None:
        companies = [c.model_dump() for c in body.companies]
    else:
        assert body.updates is not None
        companies = registry._read_document(ats_type)["companies"]
        by_key = {c["identifier"].lower(): c for c in companies}
        unknown = [u.identifier for u in body.updates if u.identifier.lower() not in by_key]
        if unknown:
            raise HTTPException(
                status_code=404, detail=f"Unknown {ats_type} companies: {', '.join(unknown)}"
            )
        for update in body.updates:
            by_key[update.identifier.lower()]["enabled"] = update.enabled
    try:
        registry.save_companies(ats_type, companies)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return _summary(registry, ats_type, reloaded=_reload(manager))


@router.post("/registries/{ats_type}/companies", status_code=201, response_model=RegistrySummary)
def add_registry_company(
    ats_type: ATSRegistryType,
    body: RegistryCompany,
    registry_dir: Path = Depends(get_registry_dir),
    manager: Optional[ConfigManager] = Depends(get_optional_config_manager),
) -> RegistrySummary:
    registry = CompanyRegistry(registry_dir)
    existing = {c["identifier"].lower() for c in registry._read_document(ats_type)["companies"]}
    if body.identifier.strip().lower() in existing:
        raise HTTPException(
            status_code=409, detail=f"'{body.identifier}' already exists in the {ats_type} registry"
        )
    try:
        registry.add_company(ats_type, body.identifier, body.name, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return _summary(registry, ats_type, reloaded=_reload(manager))
