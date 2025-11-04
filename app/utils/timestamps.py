"""Timestamp utilities for UTC handling and datetime parsing.

This module provides utilities for working with timestamps in UTC:
- Getting current UTC time
- Parsing ISO 8601 datetime strings
- Converting timezone-naive to timezone-aware UTC
- Formatting timestamps for logs and display
"""

from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime.

    Returns:
        Current UTC time with timezone info

    Example:
        >>> now = utc_now()
        >>> now.tzinfo == timezone.utc
        True
    """
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime is timezone-aware and in UTC.

    If the datetime is timezone-naive, it's treated as UTC.
    If the datetime has a different timezone, it's converted to UTC.

    Args:
        dt: Datetime to convert (can be None)

    Returns:
        Timezone-aware datetime in UTC, or None if input is None

    Example:
        >>> from datetime import datetime, timezone, timedelta
        >>> naive = datetime(2025, 11, 4, 12, 0, 0)
        >>> aware = ensure_utc(naive)
        >>> aware.tzinfo == timezone.utc
        True
    """
    if dt is None:
        return None

    # If timezone-naive, treat as UTC
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    # If timezone-aware, convert to UTC
    return dt.astimezone(timezone.utc)


def parse_iso_datetime(iso_string: str) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string to UTC datetime.

    Supports various ISO 8601 formats:
    - 2025-11-04T12:00:00Z
    - 2025-11-04T12:00:00+00:00
    - 2025-11-04T12:00:00
    - 2025-11-04

    Args:
        iso_string: ISO 8601 formatted datetime string

    Returns:
        Timezone-aware datetime in UTC, or None if parsing fails

    Example:
        >>> dt = parse_iso_datetime("2025-11-04T12:00:00Z")
        >>> dt.year == 2025 and dt.month == 11 and dt.day == 4
        True
    """
    if not iso_string or not iso_string.strip():
        return None

    try:
        # Try parsing with fromisoformat (Python 3.7+)
        # Handle 'Z' suffix (not supported by fromisoformat)
        cleaned = iso_string.strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"

        dt = datetime.fromisoformat(cleaned)
        return ensure_utc(dt)
    except (ValueError, AttributeError):
        # Fall back to trying without timezone
        try:
            # Try basic ISO format without timezone
            dt = datetime.strptime(iso_string.strip(), "%Y-%m-%dT%H:%M:%S")
            return ensure_utc(dt)
        except ValueError:
            # Try date-only format
            try:
                dt = datetime.strptime(iso_string.strip(), "%Y-%m-%d")
                return ensure_utc(dt)
            except ValueError:
                return None


def format_timestamp(dt: datetime, include_microseconds: bool = False) -> str:
    """Format a datetime as ISO 8601 string in UTC.

    Args:
        dt: Datetime to format
        include_microseconds: Whether to include microseconds in output

    Returns:
        ISO 8601 formatted string with 'Z' suffix

    Example:
        >>> from datetime import datetime, timezone
        >>> dt = datetime(2025, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        >>> format_timestamp(dt)
        '2025-11-04T12:00:00Z'
    """
    # Ensure UTC
    dt_utc = ensure_utc(dt)
    if dt_utc is None:
        return ""

    if include_microseconds:
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_timestamp_for_log(dt: datetime) -> str:
    """Format a datetime for structured logging.

    Uses ISO 8601 format without microseconds for readability.

    Args:
        dt: Datetime to format

    Returns:
        ISO 8601 formatted string with 'Z' suffix

    Example:
        >>> from datetime import datetime, timezone
        >>> dt = datetime(2025, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        >>> format_timestamp_for_log(dt)
        '2025-11-04T12:00:00Z'
    """
    return format_timestamp(dt, include_microseconds=False)


def timestamp_to_unix(dt: datetime) -> int:
    """Convert datetime to Unix timestamp (seconds since epoch).

    Args:
        dt: Datetime to convert

    Returns:
        Unix timestamp as integer (seconds since 1970-01-01 00:00:00 UTC)

    Example:
        >>> from datetime import datetime, timezone
        >>> dt = datetime(2025, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        >>> ts = timestamp_to_unix(dt)
        >>> ts > 0
        True
    """
    dt_utc = ensure_utc(dt)
    if dt_utc is None:
        return 0
    return int(dt_utc.timestamp())


def unix_to_timestamp(unix_seconds: int) -> datetime:
    """Convert Unix timestamp to datetime in UTC.

    Args:
        unix_seconds: Unix timestamp (seconds since epoch)

    Returns:
        Timezone-aware datetime in UTC

    Example:
        >>> dt = unix_to_timestamp(1730728800)
        >>> dt.tzinfo == timezone.utc
        True
    """
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc)
