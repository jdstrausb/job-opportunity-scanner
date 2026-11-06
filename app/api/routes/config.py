"""Configuration and Zero-Network Config Preview routes."""

from typing import Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import database_session, get_config_manager, get_pipeline
from app.api.schemas import (
    ConfigResponse,
    ConfigUpdateResponse,
    EnvStatus,
    JobPreviewMatch,
    PreviewRequest,
    PreviewResponse,
)
from app.config.exceptions import ConfigurationError
from app.config.manager import ConfigManager, ConfigUpdatePayload
from app.config.models import AppConfig
from app.matching.engine import KeywordMatcher
from app.normalization.models import MatchableText
from app.persistence.repositories import JobQueryFilters, JobRepository
from app.pipeline import ScanPipeline

router = APIRouter(prefix="/config", tags=["config"])

_PREVIEW_PAGE_SIZE = 500
_PREVIEW_SAMPLE_SIZE = 20


def _env_status(pipeline: ScanPipeline) -> EnvStatus:
    env = pipeline.env_config
    return EnvStatus(
        serpapi_configured=bool(env.serpapi_api_key),
        smtp_configured=bool(env.smtp_host and env.smtp_port and env.alert_to_email),
    )


def _config_fields(config: AppConfig, pipeline: ScanPipeline) -> Dict[str, object]:
    return {
        # Legacy configs only define search_criteria; surface its synthesized profile
        "search_profiles": config.search_profiles or config.get_effective_profiles(),
        "filtering": config.filtering,
        "sources": config.sources,
        "scan_interval": config.scan_interval,
        "email": config.email,
        "logging": config.logging,
        "advanced": config.advanced,
        "env_status": _env_status(pipeline),
    }


@router.get("", response_model=ConfigResponse)
def get_config(
    manager: ConfigManager = Depends(get_config_manager),
    pipeline: ScanPipeline = Depends(get_pipeline),
) -> ConfigResponse:
    return ConfigResponse(**_config_fields(manager.get_raw_config(), pipeline))  # type: ignore[arg-type]


@router.put("", response_model=ConfigUpdateResponse)
def put_config(
    payload: ConfigUpdatePayload,
    manager: ConfigManager = Depends(get_config_manager),
    pipeline: ScanPipeline = Depends(get_pipeline),
) -> ConfigUpdateResponse:
    result = manager.update_config(payload)
    return ConfigUpdateResponse(
        **_config_fields(result.raw_config, pipeline),  # type: ignore[arg-type]
        reevaluated_jobs_count=result.reevaluated_jobs_count,
    )


@router.post("/preview", response_model=PreviewResponse)
def preview_config(
    body: PreviewRequest, session: Session = Depends(database_session)
) -> PreviewResponse:
    """Dry-run candidate profiles against stored jobs; no network, no writes."""
    profiles = [p for p in body.search_profiles if p.enabled]
    names = [p.name for p in body.search_profiles]
    errors: List[str] = []
    if not profiles:
        errors.append("search_profiles: provide at least one enabled profile")
    if len(set(names)) != len(names):
        errors.append("search_profiles: profile names must be unique")
    if errors:
        raise ConfigurationError("Invalid candidate profiles", errors=errors)

    matcher = KeywordMatcher(profiles)
    repo = JobRepository(session)
    profile_counts: Dict[str, int] = {p.name: 0 for p in profiles}
    matches: List[JobPreviewMatch] = []
    evaluated = matched = newly = dropped = 0

    offset = 0
    while True:
        page = repo.query_jobs(
            JobQueryFilters(
                match_mode="all",
                sort_by="first_seen_at",
                sort_order="asc",
                limit=_PREVIEW_PAGE_SIZE,
                offset=offset,
            )
        )
        for record in page.items:
            job = record.job
            result = matcher.evaluate(job, MatchableText.from_job(job))
            evaluated += 1
            if result.is_match:
                matched += 1
                newly += 0 if job.is_match else 1
                for name in result.matched_profiles:
                    profile_counts[name] = profile_counts.get(name, 0) + 1
                matches.append(
                    JobPreviewMatch(
                        job_key=job.job_key,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        primary_profile=result.primary_profile,
                        matched_profiles=list(result.matched_profiles),
                        affinity_score=result.affinity_score,
                        score_breakdown=dict(result.score_breakdown),
                        match_snippets=list(result.snippets),
                    )
                )
            elif job.is_match:
                dropped += 1
        offset += _PREVIEW_PAGE_SIZE
        if offset >= page.total:
            break

    matches.sort(key=lambda m: m.affinity_score, reverse=True)
    return PreviewResponse(
        total_evaluated=evaluated,
        total_matched=matched,
        newly_matched_count=newly,
        dropped_match_count=dropped,
        profile_counts=profile_counts,
        sample_matches=matches[:_PREVIEW_SAMPLE_SIZE],
    )
