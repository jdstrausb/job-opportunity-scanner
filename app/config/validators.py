"""Additional validation utilities for configuration."""

import warnings
from typing import Any, Dict, List


def check_for_warnings(config_dict: Dict[str, Any]) -> List[str]:
    """
    Check configuration for potential issues and return warnings.

    Args:
        config_dict: Raw configuration dictionary

    Returns:
        List of warning messages
    """
    warning_messages = []

    # Check for disabled sources
    sources = config_dict.get("sources", [])
    for source in sources:
        if isinstance(source, dict) and not source.get("enabled", True):
            name = source.get("name", "Unknown")
            warning_messages.append(f"Source '{name}' is disabled and will be skipped")

    # Check for very short scan intervals (might trigger rate limits)
    scan_interval = config_dict.get("scan_interval", "15m")
    if isinstance(scan_interval, str):
        # Simple check for very short intervals
        if scan_interval.strip().lower() in ["1m", "2m", "3m", "4m", "PT1M", "PT2M", "PT3M", "PT4M"]:
            warning_messages.append(
                f"Short scan_interval ({scan_interval}) may trigger API rate limits"
            )

    # Check for large max_jobs_per_source
    advanced = config_dict.get("advanced", {})
    if isinstance(advanced, dict):
        max_jobs = advanced.get("max_jobs_per_source", 1000)
        if isinstance(max_jobs, int) and max_jobs > 5000:
            warning_messages.append(
                f"Large max_jobs_per_source ({max_jobs}) may cause performance issues"
            )

    # Check for duplicate terms in required_terms
    search_criteria = config_dict.get("search_criteria", {})
    if isinstance(search_criteria, dict):
        required_terms = search_criteria.get("required_terms", [])
        if isinstance(required_terms, list):
            # Normalize for duplicate detection
            normalized = [term.strip().lower() for term in required_terms if isinstance(term, str)]
            if len(normalized) != len(set(normalized)):
                duplicates = set([term for term in normalized if normalized.count(term) > 1])
                warning_messages.append(
                    f"Duplicate terms in required_terms will be deduplicated: {', '.join(sorted(duplicates))}"
                )

        # Check for very long keyword groups
        keyword_groups = search_criteria.get("keyword_groups", [])
        if isinstance(keyword_groups, list):
            for idx, group in enumerate(keyword_groups):
                if isinstance(group, list) and len(group) > 50:
                    warning_messages.append(
                        f"Keyword group {idx} has {len(group)} terms, which may slow down matching"
                    )

    return warning_messages


def emit_warnings(warning_messages: List[str]) -> None:
    """
    Emit warning messages using Python's warnings module.

    Args:
        warning_messages: List of warning messages to emit
    """
    for message in warning_messages:
        warnings.warn(message, UserWarning, stacklevel=2)
