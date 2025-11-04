"""Configuration schema models using Pydantic."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .duration import parse_duration, validate_duration_range, DurationParseError


class ATSType(str, Enum):
    """Supported ATS (Applicant Tracking System) types."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"


class LogLevel(str, Enum):
    """Logging levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogFormat(str, Enum):
    """Log output formats."""

    JSON = "json"
    KEY_VALUE = "key-value"


class SourceConfig(BaseModel):
    """Configuration for a single job source."""

    name: str = Field(..., min_length=1, description="Human-readable name for the source")
    type: ATSType = Field(..., description="ATS type (greenhouse, lever, ashby)")
    identifier: str = Field(
        ..., min_length=1, description="Company identifier used in API endpoint"
    )
    enabled: bool = Field(True, description="Whether to scan this source")

    @field_validator("name", "identifier")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        """Strip whitespace from string fields."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Field cannot be empty or whitespace-only")
        return stripped

    model_config = {"use_enum_values": True}


class SearchCriteria(BaseModel):
    """Keyword matching rules for filtering job postings."""

    required_terms: List[str] = Field(
        default_factory=list,
        description="All terms must match (AND logic)",
    )
    keyword_groups: List[List[str]] = Field(
        default_factory=list,
        description="At least one term from each group must match (OR within groups)",
    )
    exclude_terms: List[str] = Field(
        default_factory=list,
        description="Jobs containing any of these terms are filtered out",
    )

    @field_validator("required_terms", "exclude_terms")
    @classmethod
    def normalize_terms(cls, v: List[str]) -> List[str]:
        """Normalize terms: strip whitespace, convert to lowercase, remove empty strings."""
        normalized = []
        for term in v:
            stripped = term.strip().lower()
            if stripped:
                normalized.append(stripped)
        return normalized

    @field_validator("keyword_groups")
    @classmethod
    def normalize_keyword_groups(cls, v: List[List[str]]) -> List[List[str]]:
        """Normalize keyword groups: strip whitespace, convert to lowercase, remove empty."""
        normalized_groups = []
        for group in v:
            normalized_group = []
            for term in group:
                stripped = term.strip().lower()
                if stripped:
                    normalized_group.append(stripped)
            # Only include non-empty groups
            if normalized_group:
                normalized_groups.append(normalized_group)
        return normalized_groups

    @model_validator(mode="after")
    def validate_search_criteria(self):
        """Validate that search criteria has meaningful rules."""
        # Check that at least one of required_terms or keyword_groups is non-empty
        if not self.required_terms and not self.keyword_groups:
            raise ValueError(
                "Search criteria must specify at least one of: required_terms or keyword_groups"
            )

        # Check for conflicts: terms in both required and excluded
        required_set = set(self.required_terms)
        excluded_set = set(self.exclude_terms)
        conflicts = required_set & excluded_set
        if conflicts:
            raise ValueError(
                f"Terms cannot be both required and excluded: {', '.join(sorted(conflicts))}"
            )

        # Check for conflicts in keyword groups
        for group_idx, group in enumerate(self.keyword_groups):
            group_set = set(group)
            group_conflicts = group_set & excluded_set
            if group_conflicts:
                raise ValueError(
                    f"Keyword group {group_idx} contains excluded terms: "
                    f"{', '.join(sorted(group_conflicts))}"
                )

        return self


class EmailConfig(BaseModel):
    """Email notification settings."""

    use_tls: bool = Field(True, description="Use TLS/STARTTLS for secure connection")
    max_retries: int = Field(
        3, ge=0, le=10, description="Number of retry attempts for failed email sends"
    )
    retry_backoff_multiplier: float = Field(
        2.0, ge=1.0, le=5.0, description="Exponential backoff multiplier for retries"
    )
    retry_initial_delay: int = Field(
        5, ge=1, le=60, description="Initial retry delay in seconds"
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: LogLevel = Field(LogLevel.INFO, description="Log level")
    format: LogFormat = Field(
        LogFormat.KEY_VALUE, description="Log output format (json or key-value)"
    )

    model_config = {"use_enum_values": True}


class AdvancedConfig(BaseModel):
    """Advanced runtime settings."""

    http_request_timeout: int = Field(
        30, ge=5, le=300, description="Request timeout for ATS API calls (seconds)"
    )
    user_agent: str = Field(
        "JobOpportunityScanner/1.0",
        min_length=1,
        description="User-Agent string for HTTP requests",
    )
    max_jobs_per_source: int = Field(
        1000, ge=0, description="Maximum jobs to process per source (0 = unlimited)"
    )

    @field_validator("user_agent")
    @classmethod
    def strip_user_agent(cls, v: str) -> str:
        """Strip whitespace from user agent."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("user_agent cannot be empty")
        return stripped


class AppConfig(BaseModel):
    """Root configuration object for the Job Opportunity Scanner."""

    sources: List[SourceConfig] = Field(
        ..., min_length=1, description="List of job sources to monitor"
    )
    search_criteria: SearchCriteria = Field(..., description="Keyword matching rules")
    scan_interval: str = Field("15m", description="Polling interval for scanning jobs")
    email: EmailConfig = Field(default_factory=EmailConfig, description="Email settings")
    logging: LoggingConfig = Field(
        default_factory=LoggingConfig, description="Logging configuration"
    )
    advanced: AdvancedConfig = Field(
        default_factory=AdvancedConfig, description="Advanced runtime settings"
    )

    # Computed field
    scan_interval_seconds: Optional[int] = None

    @field_validator("scan_interval")
    @classmethod
    def validate_scan_interval(cls, v: str) -> str:
        """Validate and parse scan interval."""
        try:
            seconds = parse_duration(v)
            validate_duration_range(seconds, min_seconds=300, max_seconds=86400)
            return v
        except DurationParseError as e:
            raise ValueError(str(e)) from e

    @model_validator(mode="after")
    def validate_sources_and_compute_fields(self):
        """Validate sources and compute derived fields."""
        # Check that at least one source is enabled
        enabled_sources = [source for source in self.sources if source.enabled]
        if not enabled_sources:
            raise ValueError(
                "At least one source must be enabled. All sources have enabled=false."
            )

        # Check for duplicate sources (same type + identifier)
        seen_sources = set()
        for source in self.sources:
            source_key = (source.type, source.identifier)
            if source_key in seen_sources:
                raise ValueError(
                    f"Duplicate source: {source.type}/{source.identifier} appears multiple times"
                )
            seen_sources.add(source_key)

        # Compute scan interval in seconds
        try:
            self.scan_interval_seconds = parse_duration(self.scan_interval)
        except DurationParseError as e:
            # This should not happen as we already validated in field_validator
            # but include for safety
            raise ValueError(f"Failed to parse scan_interval: {e}") from e

        return self

    def get_enabled_sources(self) -> List[SourceConfig]:
        """Get list of enabled sources."""
        return [source for source in self.sources if source.enabled]

    def get_source_by_identifier(self, identifier: str) -> Optional[SourceConfig]:
        """Get a source by its identifier."""
        for source in self.sources:
            if source.identifier == identifier:
                return source
        return None
