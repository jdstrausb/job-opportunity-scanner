"""Digest builder for consolidated email notifications.

Groups matched jobs by company and sorts by posting date for improved
email readability and prioritization.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.matching.models import CandidateMatch

logger = logging.getLogger(__name__)


_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def _sortable_timestamp(value: Optional[datetime]) -> datetime:
    """Return a timezone-aware datetime suitable for sorting (missing -> earliest)."""
    if value is None:
        return _EARLIEST
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class CompanyGroup:
    """Represents a group of jobs from a single company.

    Attributes:
        company_name: Company name from Job.company
        jobs: List of CandidateMatch objects for this company
        most_recent_date: Most recent posted_at date from jobs in this group
    """

    def __init__(self, company_name: str):
        """Initialize a company group.

        Args:
            company_name: Name of the company
        """
        self.company_name = company_name
        self.jobs: List[CandidateMatch] = []
        self.most_recent_date: Optional[datetime] = None

    def add_job(self, candidate: CandidateMatch) -> None:
        """Add a job to this company group.

        Updates most_recent_date if this job has a newer posting date.

        Args:
            candidate: CandidateMatch to add to the group
        """
        self.jobs.append(candidate)

        # Track most recent posting date
        if candidate.job.posted_at:
            if self.most_recent_date is None or candidate.job.posted_at > self.most_recent_date:
                self.most_recent_date = candidate.job.posted_at

    def sort_jobs_by_date(self) -> None:
        """Sort jobs by affinity_score descending, then posted_at descending.

        Legacy match results default to affinity_score 0.0, which preserves
        pure date ordering. Jobs without posted_at sort last within a score
        tier; naive timestamps are treated as UTC so comparisons never mix
        naive and aware datetimes.
        """
        self.jobs.sort(
            key=lambda c: (
                getattr(c.match_result, "affinity_score", 0.0),
                _sortable_timestamp(c.job.posted_at),
            ),
            reverse=True,
        )


class DigestBuilder:
    """Builds consolidated email digests from matched jobs.

    Responsibilities:
    - Group matches by company name
    - Sort companies by most recent posting
    - Sort jobs within each company by posting date
    - Build structured context for template rendering
    """

    def group_by_company(self, matches: List[CandidateMatch]) -> Dict[str, CompanyGroup]:
        """Group matches by company name.

        Args:
            matches: List of candidate matches to group

        Returns:
            Dictionary mapping company name to CompanyGroup
        """
        groups: Dict[str, CompanyGroup] = {}

        for candidate in matches:
            company_name = candidate.job.company or "Unknown Company"

            if company_name not in groups:
                groups[company_name] = CompanyGroup(company_name)

            groups[company_name].add_job(candidate)

        # Sort jobs within each group by date
        for group in groups.values():
            group.sort_jobs_by_date()

        logger.debug(
            f"Grouped {len(matches)} jobs into {len(groups)} companies",
            extra={
                "event": "digest.group.completed",
                "job_count": len(matches),
                "company_count": len(groups),
            }
        )

        return groups

    def build_digest_context(
        self,
        matches: List[CandidateMatch],
        scan_id: str,
        source_name: Optional[str] = None
    ) -> Dict:
        """Build template context for digest email.

        Args:
            matches: List of all matched jobs
            scan_id: Unique scan run identifier
            source_name: Optional source config name for company_name in subject

        Returns:
            Dictionary with digest template context containing:
            - scan_id: str
            - scan_date: str (ISO8601)
            - total_matches: int
            - total_companies: int
            - company_name: str (for subject line)
            - companies: List[Dict] (sorted by most recent date)
        """
        if not matches:
            logger.info(
                "Building empty digest context",
                extra={"event": "digest.build.empty", "scan_id": scan_id}
            )
            return {
                "scan_id": scan_id,
                "scan_date": datetime.now().isoformat(),
                "total_matches": 0,
                "total_companies": 0,
                "company_name": source_name or "No Companies",
                "companies": []
            }

        # Group by company
        company_groups = self.group_by_company(matches)

        # Sort companies by most recent job posting (descending)
        sorted_companies = sorted(
            company_groups.values(),
            key=lambda g: g.most_recent_date or datetime.min,
            reverse=True
        )

        # Determine company_name for subject line
        company_name = self._determine_company_name(
            company_groups, sorted_companies, source_name
        )

        # Build company summaries
        company_summaries = []
        for group in sorted_companies:
            company_summaries.append({
                "name": group.company_name,
                "job_count": len(group.jobs),
                "most_recent_date": group.most_recent_date.isoformat() if group.most_recent_date else None,
                "jobs": [self._build_job_summary(c) for c in group.jobs]
            })

        # Build overall context
        context = {
            "scan_id": scan_id,
            "scan_date": datetime.now().isoformat(),
            "total_matches": len(matches),
            "total_companies": len(company_groups),
            "company_name": company_name,
            "companies": company_summaries
        }

        logger.info(
            f"Built digest context: {len(matches)} jobs from {len(company_groups)} companies",
            extra={
                "event": "digest.build.completed",
                "scan_id": scan_id,
                "job_count": len(matches),
                "company_count": len(company_groups),
                "company_name": company_name,
            }
        )

        return context

    def _determine_company_name(
        self,
        company_groups: Dict[str, CompanyGroup],
        sorted_companies: List[CompanyGroup],
        source_name: Optional[str]
    ) -> str:
        """Determine the company name for the digest subject.

        Priority:
        1. Use source config name if provided
        2. Use single company name if only one company
        3. Use "Multiple Companies" if multiple companies detected

        Args:
            company_groups: Dict of company groups
            sorted_companies: Sorted list of company groups
            source_name: Optional source config name

        Returns:
            Company name string for subject line
        """
        # Prefer source config name
        if source_name:
            return source_name

        # If single company, use its name
        if len(company_groups) == 1:
            return sorted_companies[0].company_name

        # Multiple companies - use fallback
        return "Multiple Companies"

    def _build_job_summary(self, candidate: CandidateMatch) -> Dict:
        """Build compact job summary for digest display.

        Args:
            candidate: Candidate match to summarize

        Returns:
            Dictionary with job summary fields:
            - title, location, url, posted_at
            - match_quality, match_summary, matched_terms
            - job_key
            - primary_profile, matched_profiles, affinity_score
        """
        job = candidate.job
        match_result = candidate.match_result

        # Extract compact match reason (first line only)
        match_summary = ""
        if match_result.summary:
            match_summary = match_result.summary.split('\n')[0]

        # Get first 3 matched terms (from required + groups)
        matched_terms = list(match_result.matched_required_terms)
        for group_matches in match_result.matched_keyword_groups:
            matched_terms.extend(group_matches)
        matched_terms = matched_terms[:3]  # Limit to first 3

        return {
            "title": job.title,
            "location": job.location or "Location not specified",
            "url": job.url,
            "posted_at": job.posted_at.isoformat() if job.posted_at else None,
            "match_quality": match_result.match_quality,
            "match_summary": match_summary,
            "matched_terms": matched_terms,
            "job_key": job.job_key,
            "primary_profile": getattr(match_result, "primary_profile", None),
            "matched_profiles": list(getattr(match_result, "matched_profiles", [])),
            "affinity_score": getattr(match_result, "affinity_score", 0.0),
        }
