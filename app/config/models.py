"""Configuration schema models using Pydantic."""

from enum import Enum
from typing import Dict, List, Optional

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


class NotificationMode(str, Enum):
    """Notification delivery modes."""

    DIGEST = "digest"
    LEGACY = "legacy"


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
    boundary_terms: List[str] = Field(
        default_factory=list,
        description="Terms that require word-boundary matches to avoid false positives (e.g., 'rails' to avoid 'guardrails')",
    )
    contextual_terms: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Terms that require context terms to be present for a match (e.g., {'rails': ['ruby']} means 'rails' only counts if 'ruby' is also present)",
    )

    @field_validator("required_terms", "exclude_terms", "boundary_terms")
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

    @field_validator("contextual_terms")
    @classmethod
    def normalize_contextual_terms(cls, v: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """Normalize contextual terms: normalize keys and values to lowercase."""
        normalized = {}
        for key, values in v.items():
            key_normalized = key.strip().lower()
            if key_normalized:
                normalized[key_normalized] = [
                    val.strip().lower() for val in values if val.strip()
                ]
        return normalized

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


class SearchProfile(SearchCriteria):
    """A named search track: keyword rules plus affinity scoring settings."""

    name: str = Field(..., min_length=1, description="Unique profile track name")
    enabled: bool = Field(True, description="Whether this search profile is active")
    affinity_weights: Dict[str, float] = Field(
        default_factory=dict,
        description="Term-to-weight mapping for affinity scoring",
    )
    title_boost_multiplier: float = Field(
        2.0,
        ge=1.0,
        le=10.0,
        description="Multiplier when matched terms appear in job title",
    )
    min_affinity_score: float = Field(
        0.0,
        ge=0.0,
        description="Minimum affinity score required to trigger a match",
    )

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        """Strip whitespace from the profile name."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Profile name cannot be empty or whitespace-only")
        return stripped

    @field_validator("affinity_weights", mode="after")
    @classmethod
    def normalize_affinity_weights(cls, v: Dict[str, float]) -> Dict[str, float]:
        """Normalize weight keys: strip whitespace and lowercase; drop empty keys."""
        normalized: Dict[str, float] = {}
        for term, weight in v.items():
            key = term.strip().lower()
            if not key:
                continue
            if key in normalized:
                raise ValueError(f"Duplicate affinity_weights term after normalization: {key}")
            normalized[key] = weight
        return normalized

    @model_validator(mode="after")
    def validate_affinity_weights_vs_exclusions(self):
        """Reject terms that are both weighted and excluded."""
        conflicts = set(self.affinity_weights) & set(self.exclude_terms)
        if conflicts:
            raise ValueError(
                f"Profile '{self.name}': terms cannot be in both affinity_weights and "
                f"exclude_terms: {', '.join(sorted(conflicts))}"
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
    notification_mode: NotificationMode = Field(
        NotificationMode.DIGEST,
        description="Notification delivery mode: digest (single email per source) or legacy (per-job emails)"
    )

    model_config = {"use_enum_values": True}


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


class FilteringRules(BaseModel):
    """Job filtering rules applied before keyword matching."""

    max_job_age_days: int = Field(
        180,
        ge=1,
        le=730,
        description="Maximum age in days for jobs (based on publishedAt or updatedAt)"
    )

    use_updated_at_fallback: bool = Field(
        True,
        description="Fall back to updatedAt if publishedAt is missing"
    )

    treat_recent_update_as_fresh: bool = Field(
        True,
        description="Treat jobs with recent updatedAt as fresh even if publishedAt is old"
    )

    require_remote: bool = Field(
        True,
        description="Filter out non-remote jobs"
    )

    remote_synonyms: List[str] = Field(
        default_factory=lambda: [
            "remote",
            "remote - us",
            "remote (us)",
            "remote us",
            "remote - usa",
            "remote usa",
            "remote - united states",
            "remote united states",
            "work from home",
            "wfh",
            "anywhere",
            "hybrid"
        ],
        description="Location strings considered as remote-acceptable (case-insensitive)"
    )

    acceptable_countries: List[str] = Field(
        default_factory=lambda: [
            "united states",
            "us",
            "usa",
            "u.s.",
            "u.s.a."
        ],
        description="Primary location countries that are acceptable (case-insensitive)"
    )

    # Remote region filtering
    remote_allowed_regions: List[str] = Field(
        default_factory=lambda: ["americas", "us", "usa", "united states"],
        description="Region tags that indicate acceptable remote locations"
    )

    remote_blocked_regions: List[str] = Field(
        default_factory=lambda: ["emea", "apac", "india", "uk", "canada"],
        description="Region tags that indicate blocked remote locations"
    )

    allow_remote_canada_if_us_allowed: bool = Field(
        True,
        description="Allow Canada remote jobs if posting explicitly allows US applicants"
    )

    us_eligibility_phrases: List[str] = Field(
        default_factory=lambda: [
            "us or canada",
            "canada or us",
            "united states or canada",
            "canada or united states",
            "open to us applicants",
            "us applicants welcome",
            "north america"
        ],
        description="Phrases in job description that indicate US applicants are eligible"
    )

    # Hybrid handling
    allow_hybrid_cities: List[str] = Field(
        default_factory=lambda: ["philadelphia, pa", "philadelphia"],
        description="Cities where hybrid work is acceptable (normalized lowercase)"
    )

    # Description-based remote detection
    require_remote_structured: bool = Field(
        True,
        description="Require structured remote signals (is_remote flag or location match); if False, allow description fallback"
    )

    remote_description_keywords: List[str] = Field(
        default_factory=list,
        description="Keywords to search in description for remote eligibility (only used if require_remote_structured=False)"
    )

    # Seniority filtering
    seniority_block_terms: List[str] = Field(
        default_factory=lambda: ["senior", "sr", "staff", "principal", "manager", "lead", "director"],
        description="Title terms that indicate seniority levels to block"
    )

    seniority_allow_terms: List[str] = Field(
        default_factory=list,
        description="Title terms that override seniority blocking (e.g., 'junior senior' to allow specific cases)"
    )

    # Experience filtering
    max_years_experience: int = Field(
        8,
        ge=0,
        le=20,
        description="Maximum years of experience to accept (soft threshold)"
    )

    hard_block_years: int = Field(
        9,
        ge=0,
        le=25,
        description="Hard block threshold - jobs requiring this many years or more are rejected"
    )

    # Role family filtering
    role_allow_terms: List[str] = Field(
        default_factory=lambda: ["developer", "engineer", "engineering"],
        description="Title terms that indicate allowed role families"
    )

    role_block_terms: List[str] = Field(
        default_factory=lambda: [
            "recruiter", "hr", "human resources", "people ops", "people operations",
            "sales", "account executive", "account manager", "business development",
            "support", "customer success", "customer service",
            "compliance", "legal", "counsel", "attorney",
            "marketing", "content", "copywriter", "designer", "product designer",
            "analyst", "data analyst", "business analyst",
            "project manager", "program manager", "scrum master",
            "qa", "quality assurance", "tester"
        ],
        description="Title terms that indicate blocked role families"
    )

    # Feature toggles
    enable_seniority_filter: bool = Field(
        True,
        description="Enable seniority-based filtering"
    )

    enable_role_filter: bool = Field(
        True,
        description="Enable role family filtering"
    )

    enable_experience_filter: bool = Field(
        True,
        description="Enable years-of-experience filtering"
    )

    # Company / industry domain filtering
    enable_domain_filter: bool = Field(
        True,
        description="Enable company and industry domain filtering"
    )

    blocked_companies: List[str] = Field(
        default_factory=list,
        description="Company names to block (case-insensitive)"
    )

    blocked_domains: List[str] = Field(
        default_factory=list,
        description="Industry/domain terms or URL hostnames to block (word-boundary matched)"
    )

    domain_allow_terms: List[str] = Field(
        default_factory=list,
        description="Terms that override blocked_domains"
    )

    @field_validator("remote_synonyms", "acceptable_countries", "remote_allowed_regions",
                     "remote_blocked_regions", "us_eligibility_phrases", "allow_hybrid_cities",
                     "remote_description_keywords", "seniority_block_terms", "seniority_allow_terms",
                     "role_allow_terms", "role_block_terms",
                     "blocked_companies", "blocked_domains", "domain_allow_terms")
    @classmethod
    def normalize_lists(cls, v: List[str]) -> List[str]:
        """Normalize to lowercase and strip whitespace."""
        return [term.strip().lower() for term in v if term.strip()]

    @model_validator(mode="after")
    def validate_filtering_rules(self):
        """Validate that filtering rules are sensible."""
        import logging
        logger = logging.getLogger(__name__)

        if self.require_remote and not self.remote_synonyms:
            raise ValueError(
                "require_remote is True but remote_synonyms list is empty"
            )

        # Warn if max_years_experience is outside typical range
        if self.max_years_experience < 3:
            logger.warning(
                f"max_years_experience is very low ({self.max_years_experience}); "
                "this may filter out most jobs"
            )
        if self.max_years_experience > 12:
            logger.warning(
                f"max_years_experience is very high ({self.max_years_experience}); "
                "this may not effectively filter senior roles"
            )

        # Ensure hard_block_years >= max_years_experience
        if self.hard_block_years < self.max_years_experience:
            raise ValueError(
                f"hard_block_years ({self.hard_block_years}) must be >= max_years_experience ({self.max_years_experience})"
            )

        return self


class AppConfig(BaseModel):
    """Root configuration object for the Job Opportunity Scanner."""

    sources: List[SourceConfig] = Field(
        ..., min_length=1, description="List of job sources to monitor"
    )
    search_criteria: Optional[SearchCriteria] = Field(
        None,
        description="Keyword matching rules (legacy single track; derived from "
        "search_profiles when omitted)",
    )
    search_profiles: List[SearchProfile] = Field(
        default_factory=list,
        description="Named multi-track search profiles",
    )
    scan_interval: str = Field("15m", description="Polling interval for scanning jobs")
    email: EmailConfig = Field(
        default_factory=EmailConfig,  # type: ignore[arg-type]
        description="Email settings",
    )
    logging: LoggingConfig = Field(
        default_factory=LoggingConfig,  # type: ignore[arg-type]
        description="Logging configuration"
    )
    advanced: AdvancedConfig = Field(
        default_factory=AdvancedConfig,  # type: ignore[arg-type]
        description="Advanced runtime settings"
    )
    filtering: FilteringRules = Field(
        default_factory=FilteringRules,  # type: ignore[arg-type]
        description="Job filtering rules",
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

        # Search profiles: names must be unique
        seen_names = set()
        for profile in self.search_profiles:
            if profile.name in seen_names:
                raise ValueError(f"Duplicate search profile name: '{profile.name}'")
            seen_names.add(profile.name)

        # Require either legacy search_criteria or at least one enabled profile
        enabled_profiles = [p for p in self.search_profiles if p.enabled]
        if self.search_criteria is None:
            if not enabled_profiles:
                raise ValueError(
                    "Configuration must define search_criteria or at least one enabled "
                    "entry in search_profiles"
                )
            # Legacy callers read search_criteria; mirror the first enabled profile
            self.search_criteria = enabled_profiles[0]

        # Compute scan interval in seconds
        try:
            self.scan_interval_seconds = parse_duration(self.scan_interval)
        except DurationParseError as e:
            # This should not happen as we already validated in field_validator
            # but include for safety
            raise ValueError(f"Failed to parse scan_interval: {e}") from e

        return self

    def get_effective_profiles(self) -> List[SearchProfile]:
        """Return the enabled search profiles to evaluate.

        Configs that only define the legacy ``search_criteria`` yield a single
        synthesized ``"default"`` profile.
        """
        enabled = [p for p in self.search_profiles if p.enabled]
        if enabled:
            return enabled
        if self.search_criteria is None:  # pragma: no cover - guarded by validator
            return []
        return [SearchProfile(name="default", **self.search_criteria.model_dump())]

    def get_enabled_sources(self) -> List[SourceConfig]:
        """Get list of enabled sources."""
        return [source for source in self.sources if source.enabled]

    def get_source_by_identifier(self, identifier: str) -> Optional[SourceConfig]:
        """Get a source by its identifier."""
        for source in self.sources:
            if source.identifier == identifier:
                return source
        return None
