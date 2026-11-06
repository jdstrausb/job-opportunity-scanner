"""Request and response models for the REST bridge (spec Section 7)."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.config.models import (
    AdvancedConfig,
    EmailConfig,
    FilteringRules,
    LoggingConfig,
    SearchProfile,
    SourceConfig,
)
from app.domain.models import DismissalScope, DismissedItem, TriageStatus
from app.persistence.repositories import JobTriageRecord
from app.pipeline.models import PipelineRunResult

ATSRegistryType = Literal["greenhouse", "lever", "ashby"]
HealthStatus = Literal["healthy", "error", "unscanned", "disabled"]


# -- jobs -------------------------------------------------------------------


class JobTriageItem(BaseModel):
    """A stored job with Match Explainability, Triage Status and Alert Status."""

    job_key: str
    source_type: str
    source_identifier: str
    external_id: str
    title: str
    company: str
    location: Optional[str] = None
    description: Optional[str] = None  # omitted from list responses
    url: str
    posted_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    first_seen_at: datetime
    last_seen_at: datetime
    secondary_locations: List[str] = Field(default_factory=list)
    is_remote: Optional[bool] = None
    location_country: Optional[str] = None
    is_match: Optional[bool] = None
    primary_profile: Optional[str] = None
    matched_profiles: List[str] = Field(default_factory=list)
    affinity_score: float = 0.0
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    match_snippets: List[str] = Field(default_factory=list)
    matched_fields: Dict[str, List[str]] = Field(default_factory=dict)
    user_status: TriageStatus = TriageStatus.NEW
    alert_status: Literal["alerted", "pending_alert", "none"]
    alert_sent_at: Optional[datetime] = None

    @classmethod
    def from_record(cls, record: JobTriageRecord, *, include_description: bool) -> "JobTriageItem":
        job = record.job
        data = job.model_dump(exclude={"content_hash"})
        if not include_description:
            data["description"] = None
        return cls(
            **data,
            alert_status=record.alert_status,  # type: ignore[arg-type]
            alert_sent_at=record.alert_sent_at,
        )


class JobCounts(BaseModel):
    matched: int
    saved: int
    applied: int
    pending_alert: int


class JobListResponse(BaseModel):
    items: List[JobTriageItem]
    total: int
    limit: int
    offset: int
    counts: JobCounts


class StatusUpdateRequest(BaseModel):
    user_status: TriageStatus


# -- dismissals -------------------------------------------------------------


class DismissedItemDTO(BaseModel):
    item_key: str
    scope: DismissalScope
    reason: Optional[str] = None
    dismissed_at: datetime

    @classmethod
    def from_domain(cls, item: DismissedItem) -> "DismissedItemDTO":
        return cls(
            item_key=item.item_key,
            scope=item.scope,
            reason=item.reason,
            dismissed_at=item.dismissed_at,
        )


class DismissalCreateRequest(BaseModel):
    scope: DismissalScope
    item_key: str = Field(..., min_length=1)
    reason: Optional[str] = None


class DismissalCreateResponse(BaseModel):
    item: DismissedItemDTO
    hidden_job_count: int


class DismissalListResponse(BaseModel):
    items: List[DismissedItemDTO]
    total: int


class DismissalDeleteResponse(BaseModel):
    restored: bool
    scope: str
    item_key: str


# -- config -----------------------------------------------------------------


class EnvStatus(BaseModel):
    """Which integrations are configured. Never carries secret values."""

    serpapi_configured: bool
    smtp_configured: bool


class ConfigResponse(BaseModel):
    search_profiles: List[SearchProfile]
    filtering: FilteringRules
    sources: List[SourceConfig]
    scan_interval: str
    email: EmailConfig
    logging: LoggingConfig
    advanced: AdvancedConfig
    env_status: EnvStatus


class ConfigUpdateResponse(ConfigResponse):
    reevaluated_jobs_count: int


class PreviewRequest(BaseModel):
    search_profiles: List[SearchProfile]


class JobPreviewMatch(BaseModel):
    job_key: str
    title: str
    company: str
    location: Optional[str] = None
    primary_profile: Optional[str] = None
    matched_profiles: List[str]
    affinity_score: float
    score_breakdown: Dict[str, float]
    match_snippets: List[str]


class PreviewResponse(BaseModel):
    total_evaluated: int
    total_matched: int
    newly_matched_count: int
    dropped_match_count: int
    profile_counts: Dict[str, int]
    sample_matches: List[JobPreviewMatch]


# -- registries -------------------------------------------------------------


class RegistryCompany(BaseModel):
    identifier: str
    name: str
    enabled: bool = True


class RegistryToggle(BaseModel):
    identifier: str
    enabled: bool


class RegistryReplaceRequest(BaseModel):
    """Either a full replacement list or a bulk enabled-toggle patch."""

    companies: Optional[List[RegistryCompany]] = None
    updates: Optional[List[RegistryToggle]] = None

    @model_validator(mode="after")
    def exactly_one(self) -> "RegistryReplaceRequest":
        if (self.companies is None) == (self.updates is None):
            raise ValueError("Provide exactly one of 'companies' or 'updates'")
        return self


class RegistrySummary(BaseModel):
    ats_type: str
    version: str
    updated_at: Optional[str] = None
    total: int
    enabled_count: int
    companies: List[RegistryCompany]
    reloaded: bool = True  # False if a running scan blocked the wildcard re-expansion


# -- sources ----------------------------------------------------------------


class ExplicitSourceHealth(BaseModel):
    name: str
    type: str
    identifier: str
    enabled: bool
    health_status: HealthStatus
    last_success_at: Optional[datetime] = None
    last_error_at: Optional[datetime] = None
    error_message: Optional[str] = None


class CompanyHealth(BaseModel):
    identifier: str
    name: str
    enabled: bool
    health_status: HealthStatus
    last_success_at: Optional[datetime] = None
    last_error_at: Optional[datetime] = None
    error_message: Optional[str] = None


class WildcardRollupHealth(BaseModel):
    name: str
    type: str
    enabled: bool
    total_companies: int
    total_enabled: int
    healthy_count: int
    error_count: int
    unscanned_count: int
    last_success_at: Optional[datetime] = None
    last_error_at: Optional[datetime] = None
    companies: List[CompanyHealth]


class SourcesResponse(BaseModel):
    explicit_sources: List[ExplicitSourceHealth]
    wildcard_rollups: List[WildcardRollupHealth]


class TestQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    num_results: int = Field(10, ge=1, le=100)

    __test__ = False  # not a pytest class


class RawJobPreview(BaseModel):
    title: str
    company: str
    location: Optional[str] = None
    url: str
    is_remote: Optional[bool] = None
    hydrated: bool


class TestQueryResponse(BaseModel):
    query: str
    count: int
    jobs: List[RawJobPreview]

    __test__ = False


# -- scan -------------------------------------------------------------------


class SourceRunStatsDTO(BaseModel):
    source_id: str
    fetched_count: int
    normalized_count: int
    filtered_count: int
    filter_reasons: Dict[str, int]
    upserted_count: int
    matched_count: int
    notified_count: int
    alerts_sent: int
    error_count: int
    duration_seconds: float
    had_errors: bool
    error_message: Optional[str] = None


class PipelineRunResultDTO(BaseModel):
    run_started_at: datetime
    run_finished_at: datetime
    total_duration_seconds: float
    total_fetched: int
    total_normalized: int
    total_filtered: int
    total_upserted: int
    total_matched: int
    total_notified: int
    total_errors: int
    had_errors: bool
    alerts_sent: int
    run_error_count: int
    skipped: bool
    source_stats: List[SourceRunStatsDTO]

    @classmethod
    def from_result(cls, result: PipelineRunResult, sanitize: Any) -> "PipelineRunResultDTO":
        """Build from a run result, scrubbing secrets from per-source error text."""
        stats = [
            SourceRunStatsDTO(
                **{
                    **vars(s),
                    "error_message": sanitize(s.error_message) if s.error_message else None,
                }
            )
            for s in result.source_stats
        ]
        data = {k: v for k, v in vars(result).items() if k != "source_stats"}
        return cls(**data, source_stats=stats)


class ScanStatusResponse(BaseModel):
    is_running: bool
    scan_interval: str
    scan_interval_seconds: int
    next_run_time: Optional[datetime] = None
    last_run_result: Optional[PipelineRunResultDTO] = None


class TriggerRequest(BaseModel):
    send_notifications: bool = False


class TriggerResponse(BaseModel):
    status: Literal["started"]
    send_notifications: bool
