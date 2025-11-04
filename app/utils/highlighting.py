"""Keyword highlighting utilities for email notifications.

This module provides utilities for highlighting matched keywords in text
for inclusion in email alerts. This helps users understand why a job
matched their search criteria.
"""

import re
from typing import List, Set


def highlight_keywords(
    text: str, keywords: List[str], marker_start: str = "**", marker_end: str = "**"
) -> str:
    """Highlight keywords in text by wrapping them with markers.

    Performs case-insensitive matching and preserves original case in output.
    Uses word boundaries to avoid partial matches within words.

    Args:
        text: Text to highlight keywords in
        keywords: List of keywords/phrases to highlight
        marker_start: Marker to insert before matched keyword (default: **)
        marker_end: Marker to insert after matched keyword (default: **)

    Returns:
        Text with keywords wrapped in markers

    Example:
        >>> text = "Looking for a Python developer with experience"
        >>> highlight_keywords(text, ["python", "developer"])
        'Looking for a **Python** **developer** with experience'
    """
    if not text or not keywords:
        return text

    # Create a copy to work with
    result = text

    # Sort keywords by length (longest first) to avoid partial replacements
    sorted_keywords = sorted(set(keywords), key=len, reverse=True)

    for keyword in sorted_keywords:
        if not keyword.strip():
            continue

        # Escape special regex characters in keyword
        escaped_keyword = re.escape(keyword)

        # Create case-insensitive pattern with word boundaries
        # For multi-word phrases, we don't use word boundaries
        if " " in keyword:
            pattern = re.compile(rf"({escaped_keyword})", re.IGNORECASE)
        else:
            pattern = re.compile(rf"\b({escaped_keyword})\b", re.IGNORECASE)

        # Replace with marked version
        result = pattern.sub(rf"{marker_start}\1{marker_end}", result)

    return result


def extract_snippets_with_keywords(
    text: str, keywords: List[str], context_chars: int = 100
) -> List[str]:
    """Extract text snippets containing keywords with surrounding context.

    Useful for showing relevant excerpts from job descriptions in email alerts.

    Args:
        text: Text to extract snippets from
        keywords: List of keywords to find
        context_chars: Number of characters of context before/after keyword

    Returns:
        List of text snippets containing keywords with context

    Example:
        >>> text = "We need a Python developer. Must have Django experience."
        >>> extract_snippets_with_keywords(text, ["python", "django"], context_chars=20)
        ['We need a Python developer. Must...',  '...Must have Django experience.']
    """
    if not text or not keywords:
        return []

    snippets = []
    text_lower = text.lower()

    for keyword in keywords:
        if not keyword.strip():
            continue

        keyword_lower = keyword.lower()

        # Find all occurrences of the keyword
        start_pos = 0
        while True:
            pos = text_lower.find(keyword_lower, start_pos)
            if pos == -1:
                break

            # Extract snippet with context
            snippet_start = max(0, pos - context_chars)
            snippet_end = min(len(text), pos + len(keyword) + context_chars)

            snippet = text[snippet_start:snippet_end].strip()

            # Add ellipsis if truncated
            if snippet_start > 0:
                snippet = "..." + snippet
            if snippet_end < len(text):
                snippet = snippet + "..."

            # Avoid duplicates
            if snippet not in snippets:
                snippets.append(snippet)

            # Move to next occurrence
            start_pos = pos + len(keyword)

    return snippets


def format_matched_terms(
    required_terms: List[str],
    matched_groups: List[List[str]],
    excluded_terms: List[str],
) -> str:
    """Format matched terms into a readable summary for email.

    Args:
        required_terms: List of required terms that matched
        matched_groups: List of matched keyword groups (at least one from each group)
        excluded_terms: List of excluded terms (should be empty for matches)

    Returns:
        Formatted string explaining what matched

    Example:
        >>> format_matched_terms(
        ...     ["remote"],
        ...     [["python"], ["senior", "lead"]],
        ...     []
        ... )
        'Required: remote\\nGroup 1: python\\nGroup 2: senior, lead'
    """
    lines = []

    if required_terms:
        terms_str = ", ".join(sorted(required_terms))
        lines.append(f"Required terms: {terms_str}")

    for idx, group in enumerate(matched_groups, start=1):
        if group:
            group_str = ", ".join(sorted(group))
            lines.append(f"Keyword group {idx}: {group_str}")

    if excluded_terms:
        terms_str = ", ".join(sorted(excluded_terms))
        lines.append(f"Excluded terms found: {terms_str}")

    return "\n".join(lines) if lines else "No specific match criteria"


def truncate_text(text: str, max_length: int = 500, suffix: str = "...") -> str:
    """Truncate text to maximum length, adding suffix if truncated.

    Tries to break at word boundaries for cleaner truncation.

    Args:
        text: Text to truncate
        max_length: Maximum length (including suffix)
        suffix: Suffix to add if truncated (default: ...)

    Returns:
        Truncated text with suffix if needed

    Example:
        >>> truncate_text("This is a very long text that needs truncating", max_length=30)
        'This is a very long text...'
    """
    if not text or len(text) <= max_length:
        return text

    # Reserve space for suffix
    truncate_at = max_length - len(suffix)

    if truncate_at <= 0:
        return suffix[:max_length]

    # Try to break at word boundary
    truncated = text[:truncate_at]

    # Find last space
    last_space = truncated.rfind(" ")
    if last_space > truncate_at * 0.8:  # Only use space if it's not too far back
        truncated = truncated[:last_space]

    return truncated.rstrip() + suffix


def normalize_for_matching(text: str) -> str:
    """Normalize text for keyword matching.

    Normalization steps:
    - Convert to lowercase
    - Strip whitespace
    - Remove extra whitespace
    - Remove common punctuation

    Args:
        text: Text to normalize

    Returns:
        Normalized text

    Example:
        >>> normalize_for_matching("  Hello,  World!  ")
        'hello world'
    """
    # Convert to lowercase
    normalized = text.lower()

    # Strip leading/trailing whitespace
    normalized = normalized.strip()

    # Replace multiple whitespace with single space
    normalized = re.sub(r"\s+", " ", normalized)

    # Remove common punctuation but keep spaces
    # Keep hyphens and apostrophes as they're meaningful in keywords
    normalized = re.sub(r"[,;.!?()[\]{}\"<>]", " ", normalized)

    # Clean up any double spaces introduced
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized
