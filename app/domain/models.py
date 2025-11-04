"""Core domain models for jobs, sources, and alerts.

This module defines the data structures used throughout the application:
- Job: normalized job posting with metadata
- RawJob: intermediate structure from ATS adapters before normalization
- AlertRecord: tracking for sent notifications
- SourceStatus: tracking source health and errors
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, v: str) -> str:
        """Validate source type is one of the supported ATS types."""
        valid_types = {"greenhouse", "lever", "ashby"}
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
        valid_types = {"greenhouse", "lever", "ashby"}
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
