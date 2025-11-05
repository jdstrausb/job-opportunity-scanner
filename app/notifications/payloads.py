"""Payload resolution for notification templates.

This module builds enriched context dictionaries from CandidateMatch objects
for use in email template rendering.
"""

from typing import Dict

from app.matching.models import CandidateMatch
from app.matching.utils import build_notification_payload


def build_notification_context(candidate: CandidateMatch) -> Dict:
    """Build enriched template context from a candidate match.

    Merges the output of build_notification_payload with additional metadata
    needed for templates, including version tracking, match quality, and
    ISO-formatted timestamps.

    Args:
        candidate: CandidateMatch containing job and match result

    Returns:
        Dictionary with all required template context keys:
        - title, company, location, url: Job metadata
        - posted_at, updated_at: ISO formatted timestamps
        - summary: Match summary text
        - snippets: List of plain text excerpts
        - snippets_highlighted: List of HTML-highlighted excerpts
        - match_quality: Match quality rating (perfect, partial, etc.)
        - search_terms: Flattened list of matched terms
        - match_reason: Alias of summary for template readability
        - first_seen_at, last_seen_at: ISO formatted job tracking times
        - source_type, source_identifier: Source metadata
        - job_key: Unique job identifier
        - version_hash: Content hash for deduplication
    """
    job = candidate.job
    match_result = candidate.match_result

    # Get base payload from matching utils
    payload = build_notification_payload(job, match_result)

    # Add additional fields for template rendering
    context = {
        **payload,
        # Add version tracking
        "version_hash": job.content_hash,
        "job_key": job.job_key,
        # Add source metadata
        "source_type": job.source_type,
        "source_identifier": job.source_identifier,
        # Add tracking timestamps (ISO format)
        "first_seen_at": job.first_seen_at.isoformat(),
        "last_seen_at": job.last_seen_at.isoformat(),
        # Ensure search_terms is present (alias from matched_terms_flat)
        "search_terms": payload["matched_terms_flat"],
        # Add match_reason as alias of summary for template clarity
        "match_reason": payload["summary"],
    }

    return context
