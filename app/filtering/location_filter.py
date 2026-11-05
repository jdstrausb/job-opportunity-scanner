"""Location-based filtering for job postings.

This module implements location-based filtering that accepts jobs based on
remote eligibility, region classification, country acceptability, and hybrid rules.
"""

import logging
from typing import Optional, Tuple

from app.domain.models import Job

from .models import FilterContext, FilterResult

logger = logging.getLogger(__name__)


class LocationFilter:
    """Filter jobs based on location metadata (remote eligibility and country).

    Checks if job is remote-eligible based on:
    - Explicit is_remote flag
    - Primary location matching remote synonyms
    - Secondary locations matching remote synonyms
    - Region classification (Americas/US vs EMEA/APAC)
    - Optional description-based fallback

    Also checks:
    - Primary location country is in acceptable countries list
    - Blocked regions (EMEA/APAC/UK/India/Canada) with Canada exception
    - Hybrid handling for specific allowed cities
    """

    def evaluate(self, job: Job, filter_context: FilterContext) -> FilterResult:
        """Evaluate if a job passes the location filter.

        Args:
            job: Job to evaluate
            filter_context: Filtering context with rules

        Returns:
            FilterResult indicating whether job passes or fails location filter
        """
        rules = filter_context.filtering_rules

        # If remote requirement is disabled, pass all jobs
        if not rules.require_remote:
            return FilterResult(
                should_process=True,
                filter_reason=None,
                filter_metadata={"location_filter": "remote_not_required"},
            )

        # Extract location data
        primary_location = job.location
        secondary_locations = job.secondary_locations or []
        is_remote_flag = job.is_remote
        location_country = job.location_country
        description = job.description or ""

        # Normalize location strings for comparison
        remote_synonyms_normalized = [s.lower() for s in rules.remote_synonyms]
        acceptable_countries_normalized = [c.lower() for c in rules.acceptable_countries]
        blocked_regions_normalized = [r.lower() for r in rules.remote_blocked_regions]
        allowed_regions_normalized = [r.lower() for r in rules.remote_allowed_regions]
        hybrid_cities_normalized = [c.lower() for c in rules.allow_hybrid_cities]
        us_eligibility_phrases_normalized = [p.lower() for p in rules.us_eligibility_phrases]

        # Check if job has any location data
        has_location_data = (
            primary_location or
            secondary_locations or
            is_remote_flag is not None
        )

        if not has_location_data:
            logger.warning(
                "Job has no location data",
                extra={
                    "event": "filtering.location.missing_data",
                    "job_key": job.job_key,
                    "job_title": job.title,
                    "company": job.company,
                    "run_id": filter_context.run_id,
                },
            )
            return FilterResult(
                should_process=False,
                filter_reason="location_missing",
                filter_metadata={"location_filter": "missing_data"},
            )

        # Check for hybrid and whether it's allowed
        is_hybrid, hybrid_city_match = self._check_hybrid(
            primary_location, secondary_locations, hybrid_cities_normalized
        )

        if is_hybrid:
            if hybrid_city_match:
                # Hybrid in allowed city - treat as remote
                logger.debug(
                    "Job is hybrid in allowed city",
                    extra={
                        "job_key": job.job_key,
                        "hybrid_city": hybrid_city_match,
                        "run_id": filter_context.run_id,
                    },
                )
                return FilterResult(
                    should_process=True,
                    filter_reason=None,
                    filter_metadata={
                        "location_filter": "hybrid_allowed",
                        "hybrid_city": hybrid_city_match,
                    },
                    is_remote_eligible=True,
                    location_country=location_country,
                    remote_signal_source="hybrid_city_match",
                    hybrid_allowed=True,
                )
            else:
                # Hybrid in non-allowed city - reject
                logger.info(
                    "Job failed location filter (hybrid in non-allowed city)",
                    extra={
                        "event": "filtering.location.rejected",
                        "job_key": job.job_key,
                        "job_title": job.title,
                        "company": job.company,
                        "filter_reason": "hybrid_not_allowed",
                        "primary_location": primary_location,
                        "run_id": filter_context.run_id,
                    },
                )
                return FilterResult(
                    should_process=False,
                    filter_reason="hybrid_not_allowed",
                    filter_metadata={
                        "location_filter": "hybrid_not_allowed",
                        "primary_location": primary_location,
                    },
                    is_remote_eligible=False,
                    hybrid_allowed=False,
                )

        # Determine remote eligibility
        is_remote_eligible = False
        remote_signal_source = None

        # Check 1: Explicit is_remote flag
        if is_remote_flag is True:
            is_remote_eligible = True
            remote_signal_source = "is_remote_flag"
            logger.debug(
                "Job is remote-eligible (explicit is_remote flag)",
                extra={
                    "job_key": job.job_key,
                    "run_id": filter_context.run_id,
                },
            )

        # Check 2: Primary location matches remote synonyms
        if not is_remote_eligible and primary_location:
            primary_normalized = primary_location.lower().strip()
            if primary_normalized in remote_synonyms_normalized:
                is_remote_eligible = True
                remote_signal_source = "primary_location_match"
                logger.debug(
                    "Job is remote-eligible (primary location matches)",
                    extra={
                        "job_key": job.job_key,
                        "primary_location": primary_location,
                        "run_id": filter_context.run_id,
                    },
                )

        # Check 3: Any secondary location matches remote synonyms
        if not is_remote_eligible and secondary_locations:
            for sec_loc in secondary_locations:
                sec_normalized = sec_loc.lower().strip()
                if sec_normalized in remote_synonyms_normalized:
                    is_remote_eligible = True
                    remote_signal_source = "secondary_location_match"
                    logger.debug(
                        "Job is remote-eligible (secondary location matches)",
                        extra={
                            "job_key": job.job_key,
                            "secondary_location": sec_loc,
                            "run_id": filter_context.run_id,
                        },
                    )
                    break

        # Check 4: Region classification in location strings
        if not is_remote_eligible:
            all_locations = [primary_location] if primary_location else []
            all_locations.extend(secondary_locations)

            for loc in all_locations:
                loc_lower = loc.lower()
                # Check for allowed regions
                for region in allowed_regions_normalized:
                    if region in loc_lower:
                        is_remote_eligible = True
                        remote_signal_source = f"region_match:{region}"
                        logger.debug(
                            "Job is remote-eligible (region match)",
                            extra={
                                "job_key": job.job_key,
                                "location": loc,
                                "region": region,
                                "run_id": filter_context.run_id,
                            },
                        )
                        break
                if is_remote_eligible:
                    break

        # Check 5: Description-based fallback (only if enabled)
        if not is_remote_eligible and not rules.require_remote_structured:
            if rules.remote_description_keywords:
                description_lower = description.lower()
                for keyword in rules.remote_description_keywords:
                    if keyword.lower() in description_lower:
                        is_remote_eligible = True
                        remote_signal_source = "description_fallback"
                        logger.debug(
                            "Job is remote-eligible (description fallback)",
                            extra={
                                "job_key": job.job_key,
                                "keyword": keyword,
                                "run_id": filter_context.run_id,
                            },
                        )
                        break

        # If not remote-eligible, reject
        if not is_remote_eligible:
            logger.info(
                "Job failed location filter (not remote)",
                extra={
                    "event": "filtering.location.rejected",
                    "job_key": job.job_key,
                    "job_title": job.title,
                    "company": job.company,
                    "filter_reason": "location_not_remote",
                    "primary_location": primary_location,
                    "secondary_locations": secondary_locations,
                    "is_remote": is_remote_flag,
                    "run_id": filter_context.run_id,
                },
            )
            return FilterResult(
                should_process=False,
                filter_reason="location_not_remote",
                filter_metadata={
                    "location_filter": "not_remote",
                    "primary_location": primary_location,
                    "secondary_locations": secondary_locations,
                    "is_remote": is_remote_flag,
                },
                is_remote_eligible=False,
            )

        # Check for blocked regions
        canada_exception_applied = False
        blocked_region_found = None
        all_locations = [primary_location] if primary_location else []
        all_locations.extend(secondary_locations)

        for loc in all_locations:
            loc_lower = loc.lower()
            for blocked_region in blocked_regions_normalized:
                if blocked_region in loc_lower:
                    blocked_region_found = blocked_region
                    break
            if blocked_region_found:
                break

        # Apply Canada exception if configured
        if blocked_region_found == "canada" and rules.allow_remote_canada_if_us_allowed:
            # Check description for US eligibility phrases
            description_lower = description.lower()
            for phrase in us_eligibility_phrases_normalized:
                if phrase in description_lower:
                    canada_exception_applied = True
                    blocked_region_found = None
                    logger.debug(
                        "Canada exception applied - US eligibility phrase found",
                        extra={
                            "job_key": job.job_key,
                            "phrase": phrase,
                            "run_id": filter_context.run_id,
                        },
                    )
                    break

        if blocked_region_found:
            logger.info(
                "Job failed location filter (blocked region)",
                extra={
                    "event": "filtering.location.region_blocked",
                    "job_key": job.job_key,
                    "job_title": job.title,
                    "company": job.company,
                    "filter_reason": "non_americas_remote",
                    "blocked_region": blocked_region_found,
                    "run_id": filter_context.run_id,
                },
            )
            return FilterResult(
                should_process=False,
                filter_reason="non_americas_remote",
                filter_metadata={
                    "location_filter": "region_blocked",
                    "blocked_region": blocked_region_found,
                },
                is_remote_eligible=True,
                remote_signal_source=remote_signal_source,
            )

        # Check country eligibility
        if location_country:
            country_normalized = location_country.lower().strip()
            if country_normalized not in acceptable_countries_normalized:
                # Check if it's Canada with exception
                if country_normalized == "canada" and canada_exception_applied:
                    pass  # Allow through
                else:
                    logger.warning(
                        "Job failed location filter (country not acceptable)",
                        extra={
                            "event": "filtering.location.country_rejected",
                            "job_key": job.job_key,
                            "job_title": job.title,
                            "company": job.company,
                            "location_country": location_country,
                            "acceptable_countries": rules.acceptable_countries,
                            "run_id": filter_context.run_id,
                        },
                    )
                    return FilterResult(
                        should_process=False,
                        filter_reason="location_country_not_acceptable",
                        filter_metadata={
                            "location_filter": "country_rejected",
                            "location_country": location_country,
                        },
                        is_remote_eligible=True,
                        location_country=location_country,
                        remote_signal_source=remote_signal_source,
                    )

        # Passed all checks
        logger.debug(
            "Job passed location filter",
            extra={
                "event": "filtering.location.passed",
                "job_key": job.job_key,
                "is_remote_eligible": is_remote_eligible,
                "location_country": location_country,
                "remote_signal_source": remote_signal_source,
                "canada_exception": canada_exception_applied,
                "run_id": filter_context.run_id,
            },
        )

        return FilterResult(
            should_process=True,
            filter_reason=None,
            filter_metadata={
                "location_filter": "passed",
                "is_remote_eligible": is_remote_eligible,
                "location_country": location_country,
                "remote_signal_source": remote_signal_source,
            },
            is_remote_eligible=is_remote_eligible,
            location_country=location_country,
            remote_signal_source=remote_signal_source,
            canada_exception_applied=canada_exception_applied if canada_exception_applied else None,
        )

    def _check_hybrid(
        self,
        primary_location: Optional[str],
        secondary_locations: list,
        hybrid_cities_normalized: list
    ) -> Tuple[bool, Optional[str]]:
        """Check if job is hybrid and if it's in an allowed city.

        Args:
            primary_location: Primary location string
            secondary_locations: List of secondary location strings
            hybrid_cities_normalized: List of allowed hybrid cities (lowercase)

        Returns:
            Tuple of (is_hybrid, matching_city_if_allowed)
        """
        all_locations = []
        if primary_location:
            all_locations.append(primary_location)
        all_locations.extend(secondary_locations)

        is_hybrid = False
        for loc in all_locations:
            loc_lower = loc.lower()
            if "hybrid" in loc_lower:
                is_hybrid = True
                break

        if not is_hybrid:
            return False, None

        # Check if any location matches allowed hybrid cities
        for loc in all_locations:
            loc_lower = loc.lower()
            for city in hybrid_cities_normalized:
                if city in loc_lower:
                    return True, city

        return True, None
