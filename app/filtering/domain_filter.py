"""Company and industry/domain filtering for job postings.

This module implements proactive suppression of postings based on:
- Blocked company names (case-insensitive exact match)
- Blocked industry/domain terms in title or description (word-boundary match)
- Blocked domains in the posting URL hostname

``domain_allow_terms`` override the industry/domain term and hostname checks
(but not the explicit company blocklist).
"""

import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

from app.domain.models import Job

from .models import FilterContext, FilterResult

logger = logging.getLogger(__name__)


def _term_pattern(term: str) -> re.Pattern[str]:
    """Build a case-insensitive word-boundary pattern for a term."""
    return re.compile(rf"\b{re.escape(term.lower())}\b", re.IGNORECASE)


def _find_term(text: str, terms: List[str]) -> Optional[str]:
    """Return the first term found in text using word-boundary matching."""
    if not text:
        return None
    for term in terms:
        if _term_pattern(term).search(text):
            return term
    return None


def _extract_hostname(url: Optional[str]) -> str:
    """Extract a lowercase hostname from a URL, or '' if unparseable."""
    if not url:
        return ""
    try:
        return (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return ""


class DomainFilter:
    """Filter jobs from blocked companies or unwanted industries."""

    def evaluate(self, job: Job, filter_context: FilterContext) -> FilterResult:
        """Evaluate if a job passes the domain filter.

        Args:
            job: Job to evaluate
            filter_context: Filtering context with rules

        Returns:
            FilterResult indicating whether job passes or fails domain filter
        """
        rules = filter_context.filtering_rules

        if not rules.enable_domain_filter:
            return FilterResult(
                should_process=True,
                filter_metadata={"domain_filter": "disabled"},
            )

        # Check 1: blocked companies (exact, case-insensitive)
        company = (job.company or "").strip().lower()
        if company:
            for blocked_company in rules.blocked_companies:
                if company == blocked_company:
                    logger.info(
                        "Job failed domain filter (company blocked)",
                        extra={
                            "event": "filtering.domain.company_blocked",
                            "job_key": job.job_key,
                            "job_title": job.title,
                            "company": job.company,
                            "filter_reason": "company_blocked",
                            "blocked_company": blocked_company,
                            "run_id": filter_context.run_id,
                        },
                    )
                    return FilterResult(
                        should_process=False,
                        filter_reason="domain_filter_company_blocked",
                        filter_metadata={
                            "domain_filter": "company_blocked",
                            "blocked_company": blocked_company,
                        },
                        domain_blocked=True,
                    )

        # Check 2: blocked domains/industry terms (unless allow-listed)
        title = job.title or ""
        description = job.description or ""
        searchable = f"{title}\n{description}"

        allow_term = _find_term(searchable, rules.domain_allow_terms)
        if allow_term is None and rules.blocked_domains:
            hostname = _extract_hostname(job.url)
            matched = _find_term(hostname, rules.blocked_domains) or _find_term(
                searchable, rules.blocked_domains
            )
            if matched:
                logger.info(
                    "Job failed domain filter (domain blocked)",
                    extra={
                        "event": "filtering.domain.domain_blocked",
                        "job_key": job.job_key,
                        "job_title": job.title,
                        "company": job.company,
                        "filter_reason": "domain_blocked",
                        "blocked_domain": matched,
                        "run_id": filter_context.run_id,
                    },
                )
                return FilterResult(
                    should_process=False,
                    filter_reason="domain_filter_domain_blocked",
                    filter_metadata={
                        "domain_filter": "domain_blocked",
                        "blocked_domain": matched,
                    },
                    domain_blocked=True,
                )

        logger.debug(
            "Job passed domain filter",
            extra={
                "event": "filtering.domain.passed",
                "job_key": job.job_key,
                "domain_allow_term": allow_term,
                "run_id": filter_context.run_id,
            },
        )
        return FilterResult(
            should_process=True,
            filter_reason=None,
            filter_metadata={"domain_filter": "passed"},
            domain_blocked=False,
        )
