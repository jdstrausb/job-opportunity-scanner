"""Utility functions for preparing match results for downstream consumers.

This module provides helpers for building notification payloads and
structuring match rationale for email templates.
"""

from typing import Dict, List

from app.domain.models import Job
from app.utils.highlighting import highlight_keywords, truncate_text

from .models import MatchResult


def build_notification_payload(job: Job, match_result: MatchResult) -> Dict:
    """Build a notification payload from a matched job.

    Assembles all the information needed for Step 7 (notifications) to send
    an email alert about this matched job.

    Args:
        job: The matched Job domain model
        match_result: MatchResult from keyword matching

    Returns:
        Dict with keys:
        - job_key: Unique job identifier
        - title: Job title
        - company: Company name
        - location: Job location (or "Remote" if None)
        - url: Direct link to job posting
        - posted_at: ISO formatted posting date
        - updated_at: ISO formatted update date (or None)
        - summary: Formatted match summary
        - snippets: List of description excerpts with keywords
        - snippets_highlighted: List of snippets with keywords wrapped in markers
        - matched_fields: Dict of field_name -> matched terms
        - matched_terms_flat: Deduplicated list of all matched terms
    """
    # Collect all matched terms for highlighting
    all_matched_terms = list(match_result.matched_required_terms)
    for group_matches in match_result.matched_keyword_groups:
        all_matched_terms.extend(group_matches)
    matched_terms_flat = sorted(set(all_matched_terms))

    # Highlight keywords in snippets for email display
    highlighted_snippets = [
        highlight_keywords(snippet, matched_terms_flat, marker_start="<b>", marker_end="</b>")
        for snippet in match_result.snippets
    ]

    # Format location
    location = job.location if job.location else "Remote"

    # Format timestamps as ISO strings
    posted_at_str = job.posted_at.isoformat() if job.posted_at else None
    updated_at_str = job.updated_at.isoformat() if job.updated_at else None

    return {
        "job_key": job.job_key,
        "title": job.title,
        "company": job.company,
        "location": location,
        "url": job.url,
        "posted_at": posted_at_str,
        "updated_at": updated_at_str,
        "summary": match_result.summary,
        "snippets": match_result.snippets,
        "snippets_highlighted": highlighted_snippets,
        "matched_fields": match_result.matched_fields,
        "matched_terms_flat": matched_terms_flat,
        "match_quality": match_result.match_quality,
    }


def build_rationale_dict(match_result: MatchResult) -> Dict:
    """Build a lightweight rationale dict for match result.

    Useful for storing alongside match results in the database or logs.

    Args:
        match_result: MatchResult to serialize

    Returns:
        Dict with match tracking information:
        - is_match: Whether it's an overall match
        - matched_required_count: Number of required terms that matched
        - matched_group_count: Number of groups with at least one match
        - excluded_found: Whether any exclude terms were found
        - matched_fields: Dict of matched terms by field
        - snippet_count: Number of relevant excerpts
    """
    return {
        "is_match": match_result.is_match,
        "matched_required_count": len(match_result.matched_required_terms),
        "required_total": len(match_result.matched_required_terms) + len(
            match_result.missing_required_terms
        ),
        "matched_group_count": len([g for g in match_result.matched_keyword_groups if g]),
        "groups_total": len(match_result.matched_keyword_groups),
        "excluded_found": len(match_result.matched_exclude_terms) > 0,
        "excluded_terms": list(match_result.matched_exclude_terms),
        "matched_fields": {k: list(v) for k, v in match_result.matched_fields.items()},
        "snippet_count": len(match_result.snippets),
        "summary": match_result.summary,
    }


def format_email_body(notification_payload: Dict, include_snippets: bool = True) -> str:
    """Format a notification payload into email body text.

    Useful for text-only email format. HTML version would be similar but with
    markup tags instead of plain text markers.

    Args:
        notification_payload: Dict from build_notification_payload()
        include_snippets: Whether to include description snippets

    Returns:
        Formatted email body string
    """
    lines = []

    # Header
    lines.append(f"Job Match: {notification_payload['title']}")
    lines.append("=" * 60)
    lines.append("")

    # Job details
    lines.append(f"Company: {notification_payload['company']}")
    lines.append(f"Location: {notification_payload['location']}")

    if notification_payload["posted_at"]:
        lines.append(f"Posted: {notification_payload['posted_at']}")

    lines.append(f"Link: {notification_payload['url']}")
    lines.append("")

    # Match summary
    lines.append("Match Details:")
    lines.append("-" * 60)
    for line in notification_payload["summary"].split("\n"):
        lines.append(f"  {line}")
    lines.append("")

    # Snippets
    if include_snippets and notification_payload["snippets"]:
        lines.append("Relevant Excerpts:")
        lines.append("-" * 60)
        for snippet in notification_payload["snippets"][:3]:  # Limit to 3 snippets
            truncated = truncate_text(snippet, max_length=300)
            lines.append(f"  {truncated}")
            lines.append("")

    return "\n".join(lines)
