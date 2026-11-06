"""Core domain models for jobs, sources, and alerts.

This module defines the data structures used throughout the application:
- Job: normalized job posting with metadata
- RawJob: intermediate structure from ATS adapters before normalization
- AlertRecord: tracking for sent notifications
- SourceStatus: tracking source health and errors
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class TriageStatus(str, Enum):
    """User triage workflow state for a stored job."""

    NEW = "new"
    SAVED = "saved"
    APPLIED = "applied"


class RawJob(BaseModel):
    """Raw job data from ATS adapter before normalization.

    This is the intermediate structure that adapters return. The normalization
    layer converts this to a Job domain model with computed fields like job_key
    and content_hash.
    """

    external_id: str = Field(..., description="Job ID from the ATS")
    title: str = Field(..., description="Job title")
    company: str = Field(..., description="Company name")
    location: Optional[str] = Field(None, description="Job location")
    description: str = Field(..., description="Full job description text")
    url: str = Field(..., description="Direct link to the job posting")
    posted_at: Optional[datetime] = Field(None, description="When job was posted (UTC)")
    updated_at: Optional[datetime] = Field(None, description="When job was last updated (UTC)")
    secondary_locations: List[str] = Field(
        default_factory=list, description="Additional location strings from ATS"
    )
    is_remote: Optional[bool] = Field(None, description="Explicit remote flag from ATS")
    location_country: Optional[str] = Field(None, description="Primary location country")

    @field_validator("external_id", "title", "company", "description", "url")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        """Strip whitespace from string fields."""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty or whitespace-only")
        return v.strip()

    @field_validator("location")
    @classmethod
    def strip_location(cls, v: Optional[str]) -> Optional[str]:
        """Strip whitespace from location field."""
        if v is None:
            return None
        stripped = v.strip()
        return stripped if stripped else None

    @field_validator("posted_at", "updated_at")
    @classmethod
    def ensure_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure datetime is timezone-aware and in UTC."""
        if v is None:
            return None
        # If timezone-naive, treat as UTC
        if v.tzinfo is None:
            from datetime import timezone

            return v.replace(tzinfo=timezone.utc)
        # If timezone-aware, convert to UTC
        from datetime import timezone

        return v.astimezone(timezone.utc)

    model_config = {"json_schema_extra": {"example": {
        "external_id": "12345",
        "title": "Senior Software Engineer",
        "company": "Example Corp",
        "location": "Remote",
        "description": "We are looking for a talented engineer...",
        "url": "https://boards.greenhouse.io/examplecorp/jobs/12345",
        "posted_at": "2025-11-01T12:00:00Z",
        "updated_at": "2025-11-02T14:30:00Z",
    }}}


class Job(BaseModel):
    """Normalized job posting with computed metadata.

    This is the canonical domain model for a job posting. It includes:
    - All raw job data from the ATS
    - Computed fields: job_key (unique identifier), content_hash (for change detection)
    - Internal tracking: first_seen_at, last_seen_at

    The job_key is computed from source_type + source_identifier + external_id to ensure
    uniqueness across all sources and prevent duplicates.

    The content_hash is computed from title + description + location to detect meaningful
    content changes even if updated_at doesn't change.
    """

    job_key: str = Field(..., description="Unique job identifier (hash of source + external_id)")
    source_type: str = Field(..., description="ATS type (greenhouse, lever, ashby)")
    source_identifier: str = Field(..., description="Company identifier in the ATS")
    external_id: str = Field(..., description="Job ID from the ATS")
    title: str = Field(..., description="Job title")
    company: str = Field(..., description="Company name")
    location: Optional[str] = Field(None, description="Job location")
    description: str = Field(..., description="Full job description text")
    url: str = Field(..., description="Direct link to the job posting")
    posted_at: Optional[datetime] = Field(None, description="When job was posted (UTC)")
    updated_at: Optional[datetime] = Field(None, description="When job was last updated (UTC)")
    first_seen_at: datetime = Field(..., description="When we first saw this job (UTC)")
    last_seen_at: datetime = Field(..., description="When we last saw this job (UTC)")
    content_hash: str = Field(
        ..., description="Hash of title + description + location for change detection"
    )
    secondary_locations: List[str] = Field(
        default_factory=list, description="Additional location strings from ATS"
    )
    is_remote: Optional[bool] = Field(None, description="Explicit remote flag from ATS")
    location_country: Optional[str] = Field(None, description="Primary location country")
    is_match: Optional[bool] = Field(
        default=None, description="Whether job matched any active Search Profile (None = not yet scored)"
    )
    primary_profile: Optional[str] = Field(default=None, description="Winning Search Profile name")
    matched_profiles: List[str] = Field(
        default_factory=list, description="All matching Search Profile names by score desc"
    )
    affinity_score: float = Field(default=0.0, description="Weighted affinity score of primary profile")
    score_breakdown: Dict[str, float] = Field(
        default_factory=dict, description="Per-term points contributing to affinity_score"
    )
    match_snippets: List[str] = Field(
        default_factory=list, description="Description excerpts with matched keywords"
    )
    matched_fields: Dict[str, List[str]] = Field(
        default_factory=dict, description="Field name to sorted list of matched terms"
    )
    user_status: TriageStatus = Field(default=TriageStatus.NEW, description="User triage workflow state")

    @field_validator("matched_fields", mode="before")
    @classmethod
    def normalize_matched_fields(cls, val: Any) -> Any:
        """Coerce set/sequence values to sorted lists so JSON encoding never fails."""
        if val is None:
            return {}
        if isinstance(val, dict):
            return {k: sorted(list(v)) for k, v in val.items() if v}
        return val

    @field_validator("user_status", mode="before")
    @classmethod
    def default_user_status(cls, val: Any) -> Any:
        """Treat NULL (legacy rows) as the 'new' triage status."""
        return TriageStatus.NEW if val is None else val

    @field_validator("matched_profiles", "match_snippets", mode="before")
    @classmethod
    def default_empty_list(cls, val: Any) -> Any:
        """Treat NULL columns as empty lists."""
        return [] if val is None else val

    @field_validator("score_breakdown", mode="before")
    @classmethod
    def default_empty_dict(cls, val: Any) -> Any:
        """Treat NULL columns as empty dicts."""
        return {} if val is None else val

    @field_validator("affinity_score", mode="before")
    @classmethod
    def default_affinity_score(cls, val: Any) -> Any:
        """Treat NULL (legacy rows) as a zero score."""
        return 0.0 if val is None else val

    def apply_match_result(self, match_result: Any) -> "Job":
        """Return a copy of this job carrying the explainability from a MatchResult.

        ``user_status`` and all other fields are left untouched.
        """
        return self.model_copy(update=self._match_fields(match_result))

    @staticmethod
    def _match_fields(match_result: Any) -> Dict[str, Any]:
        return {
            "is_match": bool(match_result.is_match),
            "primary_profile": match_result.primary_profile,
            "matched_profiles": list(match_result.matched_profiles),
            "affinity_score": float(match_result.affinity_score),
            "score_breakdown": dict(match_result.score_breakdown),
            "match_snippets": list(match_result.snippets),
            "matched_fields": {
                k: sorted(list(v)) for k, v in match_result.matched_fields.items() if v
            },
        }

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, v: str) -> str:
        """Validate source type is one of the supported ATS types."""
        valid_types = {"greenhouse", "lever", "ashby", "rss", "serpapi_google"}
        if v.lower() not in valid_types:
            raise ValueError(f"source_type must be one of {valid_types}, got: {v}")
        return v.lower()

    @field_validator("posted_at", "updated_at", "first_seen_at", "last_seen_at")
    @classmethod
    def ensure_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure datetime is timezone-aware and in UTC."""
        if v is None:
            return None
        # If timezone-naive, treat as UTC
        if v.tzinfo is None:
            from datetime import timezone

            return v.replace(tzinfo=timezone.utc)
        # If timezone-aware, convert to UTC
        from datetime import timezone

        return v.astimezone(timezone.utc)

    model_config = {"json_schema_extra": {"example": {
        "job_key": "a3f2e1d9c8b7a6f5e4d3c2b1a0987654",
        "source_type": "greenhouse",
        "source_identifier": "examplecorp",
        "external_id": "12345",
        "title": "Senior Software Engineer",
        "company": "Example Corp",
        "location": "Remote",
        "description": "We are looking for a talented engineer...",
        "url": "https://boards.greenhouse.io/examplecorp/jobs/12345",
        "posted_at": "2025-11-01T12:00:00Z",
        "updated_at": "2025-11-02T14:30:00Z",
        "first_seen_at": "2025-11-03T10:00:00Z",
        "last_seen_at": "2025-11-04T10:00:00Z",
        "content_hash": "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7",
    }}}


class AlertRecord(BaseModel):
    """Record of a sent alert notification.

    Tracks which job versions have been alerted to prevent duplicate notifications.
    The version_hash is the content_hash from the Job at the time the alert was sent.
    """

    job_key: str = Field(..., description="Job identifier this alert is for")
    version_hash: str = Field(..., description="Content hash of the job version alerted")
    sent_at: datetime = Field(..., description="When the alert was sent (UTC)")

    @field_validator("sent_at")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        """Ensure datetime is timezone-aware and in UTC."""
        # If timezone-naive, treat as UTC
        if v.tzinfo is None:
            from datetime import timezone

            return v.replace(tzinfo=timezone.utc)
        # If timezone-aware, convert to UTC
        from datetime import timezone

        return v.astimezone(timezone.utc)

    model_config = {"json_schema_extra": {"example": {
        "job_key": "a3f2e1d9c8b7a6f5e4d3c2b1a0987654",
        "version_hash": "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7",
        "sent_at": "2025-11-03T10:30:00Z",
    }}}


class SourceStatus(BaseModel):
    """Health and status tracking for a job source.

    Tracks the last successful and failed attempts to fetch jobs from a source,
    along with any error messages for debugging.
    """

    source_identifier: str = Field(..., description="Company identifier in the ATS")
    name: str = Field(..., description="Human-readable source name")
    source_type: str = Field(..., description="ATS type (greenhouse, lever, ashby)")
    last_success_at: Optional[datetime] = Field(
        None, description="Last successful fetch timestamp (UTC)"
    )
    last_error_at: Optional[datetime] = Field(None, description="Last error timestamp (UTC)")
    error_message: Optional[str] = Field(None, description="Most recent error message")

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, v: str) -> str:
        """Validate source type is one of the supported ATS types."""
        valid_types = {"greenhouse", "lever", "ashby", "rss", "serpapi_google"}
        if v.lower() not in valid_types:
            raise ValueError(f"source_type must be one of {valid_types}, got: {v}")
        return v.lower()

    @field_validator("last_success_at", "last_error_at")
    @classmethod
    def ensure_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure datetime is timezone-aware and in UTC."""
        if v is None:
            return None
        # If timezone-naive, treat as UTC
        if v.tzinfo is None:
            from datetime import timezone

            return v.replace(tzinfo=timezone.utc)
        # If timezone-aware, convert to UTC
        from datetime import timezone

        return v.astimezone(timezone.utc)

    model_config = {"json_schema_extra": {"example": {
        "source_identifier": "examplecorp",
        "name": "Example Corp",
        "source_type": "greenhouse",
        "last_success_at": "2025-11-04T10:00:00Z",
        "last_error_at": None,
        "error_message": None,
    }}}


class DismissalScope(str, Enum):
    """What a dismissal applies to."""

    JOB = "job"
    COMPANY = "company"
    SOURCE = "source"


class DismissedItem(BaseModel):
    """A persistent user mute for a job, company, or source.

    item_key is stored stripped and lowercase: the job_key for JOB scope, the
    company name for COMPANY scope, or the source identifier for SOURCE scope.
    """

    item_key: str = Field(..., description="Normalized (stripped, lowercase) key being muted")
    scope: DismissalScope = Field(DismissalScope.JOB, description="What the key refers to")
    reason: Optional[str] = Field(None, description="Optional reason for the dismissal")
    dismissed_at: datetime = Field(..., description="When the item was dismissed (UTC)")

    @field_validator("item_key")
    @classmethod
    def normalize_item_key(cls, v: str) -> str:
        """Normalize key to stripped lowercase."""
        return v.strip().lower()

    @field_validator("dismissed_at")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        """Ensure datetime is timezone-aware and in UTC."""
        from datetime import timezone

        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)
