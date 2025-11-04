"""Utility functions for hashing, time handling, and keyword highlighting."""

from .hashing import compute_content_hash, compute_job_key, hash_string
from .highlighting import (
    extract_snippets_with_keywords,
    format_matched_terms,
    highlight_keywords,
    normalize_for_matching,
    truncate_text,
)
from .timestamps import (
    ensure_utc,
    format_timestamp,
    format_timestamp_for_log,
    parse_iso_datetime,
    timestamp_to_unix,
    unix_to_timestamp,
    utc_now,
)

__all__ = [
    # Hashing
    "compute_job_key",
    "compute_content_hash",
    "hash_string",
    # Timestamps
    "utc_now",
    "ensure_utc",
    "parse_iso_datetime",
    "format_timestamp",
    "format_timestamp_for_log",
    "timestamp_to_unix",
    "unix_to_timestamp",
    # Highlighting
    "highlight_keywords",
    "extract_snippets_with_keywords",
    "format_matched_terms",
    "truncate_text",
    "normalize_for_matching",
]
