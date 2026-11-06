"""Data models for the filtering layer.

This module defines the data structures used for job filtering operations,
including filter context and filter results.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from app.config.models import FilteringRules


@dataclass
class FilterContext:
    """Context for filtering decisions.

    Attributes:
        filtering_rules: Configuration for filtering rules
        scan_timestamp: Current scan timestamp (UTC)
        run_id: Unique identifier for this scan run
    """

    filtering_rules: FilteringRules
    scan_timestamp: datetime
    run_id: str


@dataclass
class FilterResult:
    """Result of filtering a job.

    Attributes:
        should_process: True if job passes filters, False if filtered out
        filter_reason: Reason for filtering (None if passed)
        filter_metadata: Additional metadata for logging
        age_days: Job age in days (None if age couldn't be determined)
        is_remote_eligible: Whether job is considered remote-eligible
        location_country: Primary location country
        remote_signal_source: How remote eligibility was determined (is_remote_flag, location_match, region_match, description_fallback)
        hybrid_allowed: Whether job was allowed as hybrid in acceptable city
        canada_exception_applied: Whether Canada exception was applied for US eligibility
        seniority_blocked: Whether seniority filter blocked the job
        role_blocked: Whether role family filter blocked the job
        experience_blocked: Whether experience filter blocked the job
        detected_years_experience: Years of experience detected in job description (if any)
        domain_blocked: Whether the company/industry domain filter blocked the job
    """

    should_process: bool
    filter_reason: Optional[str] = None
    filter_metadata: Dict[str, Any] = field(default_factory=dict)
    age_days: Optional[int] = None
    is_remote_eligible: Optional[bool] = None
    location_country: Optional[str] = None
    remote_signal_source: Optional[str] = None
    hybrid_allowed: Optional[bool] = None
    canada_exception_applied: Optional[bool] = None
    seniority_blocked: Optional[bool] = None
    role_blocked: Optional[bool] = None
    experience_blocked: Optional[bool] = None
    detected_years_experience: Optional[int] = None
    domain_blocked: Optional[bool] = None
