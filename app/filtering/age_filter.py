"""Age-based filtering for job postings.

This module implements age-based filtering that excludes jobs older than
a configured threshold based on publishedAt or updatedAt timestamps.
"""

import logging
from datetime import datetime

from app.domain.models import Job

from .models import FilterContext, FilterResult

logger = logging.getLogger(__name__)


class AgeFilter:
    """Filter jobs based on age (time since published or updated).

    Uses publishedAt as primary timestamp, falls back to updatedAt if missing.
    Optionally treats recently updated jobs as fresh even if published long ago.
    """

    def evaluate(self, job: Job, filter_context: FilterContext) -> FilterResult:
        """Evaluate if a job passes the age filter.

        Args:
            job: Job to evaluate
            filter_context: Filtering context with rules and scan timestamp

        Returns:
            FilterResult indicating whether job passes or fails age filter
        """
        rules = filter_context.filtering_rules
        scan_timestamp = filter_context.scan_timestamp

        # Extract timestamps
        published_at = job.posted_at
        updated_at = job.updated_at

        # Handle missing timestamps
        if published_at is None and updated_at is None:
            logger.warning(
                "Job has no timestamps (published_at and updated_at both None)",
                extra={
                    "event": "filtering.age.no_timestamps",
                    "job_key": job.job_key,
                    "job_title": job.title,
                    "company": job.company,
                    "run_id": filter_context.run_id,
                },
            )
            # Cannot determine age - pass by default (safer to include)
            return FilterResult(
                should_process=True,
                filter_reason=None,
                filter_metadata={"age_filter": "no_timestamps_pass"},
            )

        # Calculate ages
        age_published = None
        age_updated = None

        if published_at:
            age_delta = scan_timestamp - published_at
            age_published = age_delta.days

            # Check for clock skew (future timestamp)
            if age_published < 0:
                logger.warning(
                    "Job publishedAt is in the future (clock skew)",
                    extra={
                        "event": "filtering.age.clock_skew",
                        "job_key": job.job_key,
                        "published_at": published_at.isoformat(),
                        "scan_timestamp": scan_timestamp.isoformat(),
                        "age_days": age_published,
                        "run_id": filter_context.run_id,
                    },
                )
                # Clock skew - pass by default
                return FilterResult(
                    should_process=True,
                    filter_reason=None,
                    filter_metadata={"age_filter": "clock_skew_pass"},
                    age_days=age_published,
                )

        if updated_at:
            age_delta = scan_timestamp - updated_at
            age_updated = age_delta.days

        # Apply filtering logic
        threshold = rules.max_job_age_days

        # Primary check: publishedAt within threshold
        if age_published is not None and age_published <= threshold:
            logger.debug(
                "Job passed age filter (publishedAt within threshold)",
                extra={
                    "event": "filtering.age.passed",
                    "job_key": job.job_key,
                    "age_days": age_published,
                    "threshold_days": threshold,
                    "run_id": filter_context.run_id,
                },
            )
            return FilterResult(
                should_process=True,
                filter_reason=None,
                filter_metadata={"age_filter": "published_pass"},
                age_days=age_published,
            )

        # Fallback check: use updatedAt if publishedAt missing
        if published_at is None and rules.use_updated_at_fallback:
            if age_updated is not None and age_updated <= threshold:
                logger.debug(
                    "Job passed age filter (updatedAt fallback)",
                    extra={
                        "event": "filtering.age.passed",
                        "job_key": job.job_key,
                        "age_days": age_updated,
                        "threshold_days": threshold,
                        "using_field": "updated_at",
                        "run_id": filter_context.run_id,
                    },
                )
                return FilterResult(
                    should_process=True,
                    filter_reason=None,
                    filter_metadata={"age_filter": "updated_fallback_pass"},
                    age_days=age_updated,
                )

        # Fresh update check: recent updatedAt overrides old publishedAt
        if rules.treat_recent_update_as_fresh:
            if age_updated is not None and age_updated <= threshold:
                logger.info(
                    "Job passed age filter (recent update)",
                    extra={
                        "event": "filtering.age.recent_update",
                        "job_key": job.job_key,
                        "published_age_days": age_published,
                        "updated_age_days": age_updated,
                        "threshold_days": threshold,
                        "run_id": filter_context.run_id,
                    },
                )
                return FilterResult(
                    should_process=True,
                    filter_reason=None,
                    filter_metadata={"age_filter": "recent_update_pass"},
                    age_days=age_updated,
                )

        # Failed age filter
        primary_age = age_published if age_published is not None else age_updated
        logger.info(
            "Job failed age filter (exceeds threshold)",
            extra={
                "event": "filtering.age.rejected",
                "job_key": job.job_key,
                "job_title": job.title,
                "company": job.company,
                "age_days": primary_age,
                "threshold_days": threshold,
                "published_age_days": age_published,
                "updated_age_days": age_updated,
                "run_id": filter_context.run_id,
            },
        )

        return FilterResult(
            should_process=False,
            filter_reason="job_age_exceeds_threshold",
            filter_metadata={
                "age_filter": "rejected",
                "age_days": primary_age,
                "threshold_days": threshold,
                "published_age_days": age_published,
                "updated_age_days": age_updated,
            },
            age_days=primary_age,
        )
