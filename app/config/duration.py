"""Duration parsing utilities for configuration."""

import re
from typing import Union


class DurationParseError(ValueError):
    """Raised when a duration string cannot be parsed."""

    pass


def parse_duration(duration_str: str) -> int:
    """
    Parse a duration string to seconds.

    Supports both human-readable formats and ISO-8601 durations:
    - Human-readable: "15m", "1h", "30s", "2d"
    - ISO-8601: "PT15M", "PT1H", "PT30S", "P2D"

    Args:
        duration_str: Duration string to parse

    Returns:
        Duration in seconds

    Raises:
        DurationParseError: If the duration string is invalid

    Examples:
        >>> parse_duration("15m")
        900
        >>> parse_duration("PT15M")
        900
        >>> parse_duration("1h")
        3600
        >>> parse_duration("PT1H")
        3600
    """
    duration_str = duration_str.strip()

    if not duration_str:
        raise DurationParseError("Duration string cannot be empty")

    # Try ISO-8601 format first (starts with P)
    if duration_str.upper().startswith("P"):
        return _parse_iso8601_duration(duration_str)

    # Try human-readable format
    return _parse_human_readable_duration(duration_str)


def _parse_iso8601_duration(duration_str: str) -> int:
    """
    Parse ISO-8601 duration format.

    Supports: P[n]D, PT[n]H[n]M[n]S
    Examples: P2D, PT1H30M, PT15M, PT30S

    Args:
        duration_str: ISO-8601 duration string

    Returns:
        Duration in seconds

    Raises:
        DurationParseError: If the format is invalid
    """
    duration_str = duration_str.upper()

    # Pattern for ISO-8601 duration
    # P[n]DT[n]H[n]M[n]S or simplified versions
    pattern = r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$"
    match = re.match(pattern, duration_str)

    if not match:
        raise DurationParseError(
            f"Invalid ISO-8601 duration format: '{duration_str}'. "
            "Expected format like 'P2D', 'PT1H30M', 'PT15M', or 'PT30S'"
        )

    days, hours, minutes, seconds = match.groups()

    total_seconds = 0
    if days:
        total_seconds += int(days) * 86400  # 24 * 60 * 60
    if hours:
        total_seconds += int(hours) * 3600  # 60 * 60
    if minutes:
        total_seconds += int(minutes) * 60
    if seconds:
        total_seconds += int(float(seconds))

    if total_seconds == 0:
        raise DurationParseError(f"Duration cannot be zero: '{duration_str}'")

    return total_seconds


def _parse_human_readable_duration(duration_str: str) -> int:
    """
    Parse human-readable duration format.

    Supports: 30s, 15m, 1h, 2d
    Can combine multiple units: 1h30m, 2d12h

    Args:
        duration_str: Human-readable duration string

    Returns:
        Duration in seconds

    Raises:
        DurationParseError: If the format is invalid
    """
    # Pattern to match number + unit combinations
    pattern = r"(\d+)\s*([smhd])"
    matches = re.findall(pattern, duration_str.lower())

    if not matches:
        raise DurationParseError(
            f"Invalid duration format: '{duration_str}'. "
            "Expected format like '15m', '1h', '30s', '2d', or combinations like '1h30m'"
        )

    # Check if the entire string was parsed (no invalid characters)
    parsed_str = "".join(f"{num}{unit}" for num, unit in matches)
    cleaned_input = re.sub(r"\s+", "", duration_str.lower())
    if parsed_str != cleaned_input:
        raise DurationParseError(
            f"Invalid characters in duration: '{duration_str}'. "
            "Use only digits and units: s (seconds), m (minutes), h (hours), d (days)"
        )

    # Convert to seconds
    unit_multipliers = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }

    total_seconds = 0
    for num, unit in matches:
        total_seconds += int(num) * unit_multipliers[unit]

    if total_seconds == 0:
        raise DurationParseError(f"Duration cannot be zero: '{duration_str}'")

    return total_seconds


def validate_duration_range(
    duration_seconds: int,
    min_seconds: int = 300,  # 5 minutes
    max_seconds: int = 86400,  # 24 hours
) -> None:
    """
    Validate that a duration is within acceptable range.

    Args:
        duration_seconds: Duration in seconds to validate
        min_seconds: Minimum allowed duration (default: 5 minutes)
        max_seconds: Maximum allowed duration (default: 24 hours)

    Raises:
        DurationParseError: If duration is outside the valid range
    """
    if duration_seconds < min_seconds:
        min_human = _seconds_to_human_readable(min_seconds)
        actual_human = _seconds_to_human_readable(duration_seconds)
        raise DurationParseError(
            f"Scan interval too short: {actual_human}. Minimum is {min_human}."
        )

    if duration_seconds > max_seconds:
        max_human = _seconds_to_human_readable(max_seconds)
        actual_human = _seconds_to_human_readable(duration_seconds)
        raise DurationParseError(
            f"Scan interval too long: {actual_human}. Maximum is {max_human}."
        )


def _seconds_to_human_readable(seconds: int) -> str:
    """
    Convert seconds to human-readable format.

    Args:
        seconds: Number of seconds

    Returns:
        Human-readable string (e.g., "15 minutes", "1 hour", "2 days")
    """
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''}"
