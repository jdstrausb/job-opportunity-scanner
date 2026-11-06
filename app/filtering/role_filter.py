"""Role-based filtering for job postings.

This module implements filtering based on:
- Seniority level (senior, staff, principal, etc.)
- Years of experience requirements
- Role family (developer/engineer vs non-engineering roles)
"""

import logging
import re
from typing import Optional, Tuple

from app.domain.models import Job

from .models import FilterContext, FilterResult

logger = logging.getLogger(__name__)

# Regex patterns for extracting years of experience
YEARS_PATTERNS = [
    # "X+ years" or "X years" patterns
    re.compile(r'(\d+)\+?\s*(?:years?|yrs?)(?:\s+of)?\s+(?:experience|exp)', re.IGNORECASE),
    # "minimum X years" or "at least X years"
    re.compile(r'(?:minimum|at least|min\.?)\s+(\d+)\s*(?:years?|yrs?)', re.IGNORECASE),
    # "X-Y years" range patterns - take the minimum
    re.compile(r'(\d+)\s*[-–]\s*\d+\s*(?:years?|yrs?)', re.IGNORECASE),
    # "X to Y years" range patterns - take the minimum
    re.compile(r'(\d+)\s+to\s+\d+\s*(?:years?|yrs?)', re.IGNORECASE),
]


class RoleFilter:
    """Filter jobs based on role characteristics.

    Filters jobs based on:
    - Seniority terms in title (senior, staff, principal, etc.)
    - Years of experience requirements in description
    - Role family (developer/engineer vs other roles)
    """

    def evaluate(self, job: Job, filter_context: FilterContext) -> FilterResult:
        """Evaluate if a job passes the role filter.

        Args:
            job: Job to evaluate
            filter_context: Filtering context with rules

        Returns:
            FilterResult indicating whether job passes or fails role filter
        """
        rules = filter_context.filtering_rules
        title = job.title or ""
        title_lower = title.lower()
        description = job.description or ""
        description_lower = description.lower()

        # Initialize result metadata
        seniority_blocked = False
        role_blocked = False
        experience_blocked = False
        detected_years = None

        # Check 1: Seniority filter (if enabled)
        if rules.enable_seniority_filter:
            seniority_blocked, seniority_reason = self._check_seniority(
                title_lower,
                rules.seniority_block_terms,
                rules.seniority_allow_terms,
            )

            if seniority_blocked:
                logger.info(
                    "Job failed role filter (seniority blocked)",
                    extra={
                        "event": "filtering.role.seniority_blocked",
                        "job_key": job.job_key,
                        "job_title": job.title,
                        "company": job.company,
                        "filter_reason": "seniority_blocked",
                        "seniority_reason": seniority_reason,
                        "run_id": filter_context.run_id,
                    },
                )
                return FilterResult(
                    should_process=False,
                    filter_reason="role_filter_seniority_blocked",
                    filter_metadata={
                        "role_filter": "seniority_blocked",
                        "blocked_term": seniority_reason,
                    },
                    seniority_blocked=True,
                )

        # Check 2: Role family filter (if enabled)
        if rules.enable_role_filter:
            has_allow_term = self._has_any_term(title_lower, rules.role_allow_terms)
            has_block_term, blocked_term = self._find_blocking_term(
                title_lower, rules.role_block_terms
            )

            # Reject if title contains blocked role term
            if has_block_term:
                role_blocked = True
                logger.info(
                    "Job failed role filter (non-engineering role)",
                    extra={
                        "event": "filtering.role.family_blocked",
                        "job_key": job.job_key,
                        "job_title": job.title,
                        "company": job.company,
                        "filter_reason": "non_engineering",
                        "blocked_term": blocked_term,
                        "run_id": filter_context.run_id,
                    },
                )
                return FilterResult(
                    should_process=False,
                    filter_reason="role_filter_non_engineering",
                    filter_metadata={
                        "role_filter": "non_engineering",
                        "blocked_term": blocked_term,
                    },
                    role_blocked=True,
                )

            # Reject if title doesn't have any allowed role term
            if not has_allow_term:
                role_blocked = True
                logger.info(
                    "Job failed role filter (missing engineering term)",
                    extra={
                        "event": "filtering.role.missing_allow_term",
                        "job_key": job.job_key,
                        "job_title": job.title,
                        "company": job.company,
                        "filter_reason": "missing_engineering_term",
                        "run_id": filter_context.run_id,
                    },
                )
                return FilterResult(
                    should_process=False,
                    filter_reason="role_filter_missing_engineering",
                    filter_metadata={
                        "role_filter": "missing_engineering_term",
                    },
                    role_blocked=True,
                )

        # Check 3: Experience filter (if enabled)
        if rules.enable_experience_filter:
            detected_years = self._extract_years_experience(description_lower)

            if detected_years is not None:
                if detected_years >= rules.hard_block_years:
                    experience_blocked = True
                    logger.info(
                        "Job failed role filter (experience too high)",
                        extra={
                            "event": "filtering.role.experience_blocked",
                            "job_key": job.job_key,
                            "job_title": job.title,
                            "company": job.company,
                            "filter_reason": "experience_blocked",
                            "detected_years": detected_years,
                            "hard_block_years": rules.hard_block_years,
                            "run_id": filter_context.run_id,
                        },
                    )
                    return FilterResult(
                        should_process=False,
                        filter_reason="role_filter_experience_blocked",
                        filter_metadata={
                            "role_filter": "experience_blocked",
                            "detected_years": detected_years,
                            "hard_block_years": rules.hard_block_years,
                        },
                        experience_blocked=True,
                        detected_years_experience=detected_years,
                    )

        # Passed all checks
        logger.debug(
            "Job passed role filter",
            extra={
                "event": "filtering.role.passed",
                "job_key": job.job_key,
                "detected_years": detected_years,
                "run_id": filter_context.run_id,
            },
        )

        return FilterResult(
            should_process=True,
            filter_reason=None,
            filter_metadata={
                "role_filter": "passed",
                "detected_years": detected_years,
            },
            seniority_blocked=False,
            role_blocked=False,
            experience_blocked=False,
            detected_years_experience=detected_years,
        )

    def _check_seniority(
        self,
        title_lower: str,
        block_terms: list,
        allow_terms: list,
    ) -> Tuple[bool, Optional[str]]:
        """Check if title contains blocked seniority terms.

        Args:
            title_lower: Lowercase job title
            block_terms: List of seniority terms to block
            allow_terms: List of terms that override blocking

        Returns:
            Tuple of (is_blocked, blocking_term)
        """
        # First check if any allow terms are present (override)
        for allow_term in allow_terms:
            if allow_term.lower() in title_lower:
                return False, None

        # Check for blocking terms using word boundaries
        for block_term in block_terms:
            block_lower = block_term.lower()
            # Use word boundary regex for better matching
            pattern = re.compile(rf'\b{re.escape(block_lower)}\b', re.IGNORECASE)
            if pattern.search(title_lower):
                return True, block_term

        return False, None

    def _has_any_term(self, text: str, terms: list) -> bool:
        """Check if text contains any of the terms.

        Args:
            text: Text to search (should be lowercase)
            terms: List of terms to search for

        Returns:
            True if any term is found
        """
        for term in terms:
            term_lower = term.lower()
            # Use word boundary matching for better accuracy
            pattern = re.compile(rf'\b{re.escape(term_lower)}\b', re.IGNORECASE)
            if pattern.search(text):
                return True
        return False

    def _find_blocking_term(
        self, text: str, block_terms: list
    ) -> Tuple[bool, Optional[str]]:
        """Find if any blocking term is present in text.

        Args:
            text: Text to search (should be lowercase)
            block_terms: List of terms to block

        Returns:
            Tuple of (is_found, found_term)
        """
        for term in block_terms:
            term_lower = term.lower()
            # Use word boundary matching
            pattern = re.compile(rf'\b{re.escape(term_lower)}\b', re.IGNORECASE)
            if pattern.search(text):
                return True, term
        return False, None

    def _extract_years_experience(self, description_lower: str) -> Optional[int]:
        """Extract years of experience requirement from job description.

        Looks for patterns like:
        - "5+ years of experience"
        - "minimum 7 years"
        - "5-8 years" (takes minimum)
        - "at least 6 years"

        Args:
            description_lower: Lowercase job description

        Returns:
            Detected years of experience, or None if not found
        """
        all_matches = []

        for pattern in YEARS_PATTERNS:
            matches = pattern.findall(description_lower)
            for match in matches:
                try:
                    years = int(match)
                    if 0 < years <= 30:  # Sanity check
                        all_matches.append(years)
                except (ValueError, TypeError):
                    continue

        if not all_matches:
            return None

        # Return the minimum years found (most common/realistic requirement)
        # Some postings have multiple requirements; taking min is more permissive
        return min(all_matches)
