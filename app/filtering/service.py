"""Filtering service that orchestrates all job filters.

This module provides a unified service for evaluating jobs against
all configured filters (age, location, role, and domain).
"""

import logging

from app.domain.models import Job

from .age_filter import AgeFilter
from .domain_filter import DomainFilter
from .location_filter import LocationFilter
from .role_filter import RoleFilter
from .models import FilterContext, FilterResult

logger = logging.getLogger(__name__)


class FilteringService:
    """Service that orchestrates job filtering operations.

    Runs all configured filters in sequence and returns aggregated result.
    """

    def __init__(self):
        """Initialize filtering service with filter instances."""
        self.age_filter = AgeFilter()
        self.location_filter = LocationFilter()
        self.role_filter = RoleFilter()
        self.domain_filter = DomainFilter()

    def evaluate_job(self, job: Job, filter_context: FilterContext) -> FilterResult:
        """Evaluate a job against all filters.

        Filters are run in sequence:
        1. Age filter
        2. Location filter
        3. Role filter (seniority, experience, role family)
        4. Domain filter (blocked companies, industries, and URL domains)

        If any filter fails, remaining filters are skipped and failure is returned.

        Args:
            job: Job to evaluate
            filter_context: Filtering context with rules and metadata

        Returns:
            FilterResult with aggregated filtering decision
        """
        # Run age filter first
        age_result = self.age_filter.evaluate(job, filter_context)
        if not age_result.should_process:
            # Age filter failed - return immediately
            logger.debug(
                "Job filtered out by age filter",
                extra={
                    "event": "filtering.job.evaluated",
                    "job_key": job.job_key,
                    "should_process": False,
                    "filter_reason": age_result.filter_reason,
                    "run_id": filter_context.run_id,
                },
            )
            return age_result

        # Run location filter
        location_result = self.location_filter.evaluate(job, filter_context)
        if not location_result.should_process:
            # Location filter failed
            logger.debug(
                "Job filtered out by location filter",
                extra={
                    "event": "filtering.job.evaluated",
                    "job_key": job.job_key,
                    "should_process": False,
                    "filter_reason": location_result.filter_reason,
                    "run_id": filter_context.run_id,
                },
            )
            # Merge age metadata into location result
            location_result.filter_metadata.update(age_result.filter_metadata)
            location_result.age_days = age_result.age_days
            return location_result

        # Run role filter
        role_result = self.role_filter.evaluate(job, filter_context)
        if not role_result.should_process:
            # Role filter failed
            logger.debug(
                "Job filtered out by role filter",
                extra={
                    "event": "filtering.job.evaluated",
                    "job_key": job.job_key,
                    "should_process": False,
                    "filter_reason": role_result.filter_reason,
                    "run_id": filter_context.run_id,
                },
            )
            # Merge previous metadata into role result
            role_result.filter_metadata.update(age_result.filter_metadata)
            role_result.filter_metadata.update(location_result.filter_metadata)
            role_result.age_days = age_result.age_days
            role_result.is_remote_eligible = location_result.is_remote_eligible
            role_result.location_country = location_result.location_country
            role_result.remote_signal_source = location_result.remote_signal_source
            role_result.hybrid_allowed = location_result.hybrid_allowed
            role_result.canada_exception_applied = location_result.canada_exception_applied
            return role_result

        # Run domain filter
        domain_result = self.domain_filter.evaluate(job, filter_context)
        if not domain_result.should_process:
            # Domain filter failed
            logger.debug(
                "Job filtered out by domain filter",
                extra={
                    "event": "filtering.job.evaluated",
                    "job_key": job.job_key,
                    "should_process": False,
                    "filter_reason": domain_result.filter_reason,
                    "run_id": filter_context.run_id,
                },
            )
            # Merge previous metadata into domain result
            domain_result.filter_metadata.update(age_result.filter_metadata)
            domain_result.filter_metadata.update(location_result.filter_metadata)
            domain_result.filter_metadata.update(role_result.filter_metadata)
            domain_result.age_days = age_result.age_days
            domain_result.is_remote_eligible = location_result.is_remote_eligible
            domain_result.location_country = location_result.location_country
            domain_result.remote_signal_source = location_result.remote_signal_source
            domain_result.hybrid_allowed = location_result.hybrid_allowed
            domain_result.canada_exception_applied = location_result.canada_exception_applied
            domain_result.seniority_blocked = role_result.seniority_blocked
            domain_result.role_blocked = role_result.role_blocked
            domain_result.experience_blocked = role_result.experience_blocked
            domain_result.detected_years_experience = role_result.detected_years_experience
            return domain_result

        # All filters passed
        logger.debug(
            "Job passed all filters",
            extra={
                "event": "filtering.job.evaluated",
                "job_key": job.job_key,
                "should_process": True,
                "run_id": filter_context.run_id,
            },
        )

        # Merge metadata from all filters
        merged_metadata = {}
        merged_metadata.update(age_result.filter_metadata)
        merged_metadata.update(location_result.filter_metadata)
        merged_metadata.update(role_result.filter_metadata)
        merged_metadata.update(domain_result.filter_metadata)

        return FilterResult(
            should_process=True,
            filter_reason=None,
            filter_metadata=merged_metadata,
            age_days=age_result.age_days,
            is_remote_eligible=location_result.is_remote_eligible,
            location_country=location_result.location_country,
            remote_signal_source=location_result.remote_signal_source,
            hybrid_allowed=location_result.hybrid_allowed,
            canada_exception_applied=location_result.canada_exception_applied,
            seniority_blocked=role_result.seniority_blocked,
            role_blocked=role_result.role_blocked,
            experience_blocked=role_result.experience_blocked,
            detected_years_experience=role_result.detected_years_experience,
            domain_blocked=domain_result.domain_blocked,
        )
