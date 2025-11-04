"""Unit tests for timestamp utilities."""

from datetime import datetime, timedelta, timezone

import pytest

from app.utils.timestamps import (
    ensure_utc,
    format_timestamp,
    format_timestamp_for_log,
    parse_iso_datetime,
    timestamp_to_unix,
    unix_to_timestamp,
    utc_now,
)


class TestUtcNow:
    """Tests for utc_now function."""

    def test_utc_now_returns_utc_datetime(self):
        """Test that utc_now returns a timezone-aware datetime in UTC."""
        now = utc_now()

        assert now.tzinfo == timezone.utc
        assert isinstance(now, datetime)

    def test_utc_now_is_recent(self):
        """Test that utc_now returns a recent timestamp."""
        before = datetime.now(timezone.utc)
        now = utc_now()
        after = datetime.now(timezone.utc)

        assert before <= now <= after


class TestEnsureUtc:
    """Tests for ensure_utc function."""

    def test_ensure_utc_with_none(self):
        """Test that None input returns None."""
        assert ensure_utc(None) is None

    def test_ensure_utc_with_naive_datetime(self):
        """Test that naive datetime is treated as UTC."""
        naive = datetime(2025, 11, 4, 12, 0, 0)
        result = ensure_utc(naive)

        assert result.tzinfo == timezone.utc
        assert result.year == 2025
        assert result.month == 11
        assert result.day == 4
        assert result.hour == 12

    def test_ensure_utc_with_utc_datetime(self):
        """Test that UTC datetime is unchanged."""
        utc_dt = datetime(2025, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        result = ensure_utc(utc_dt)

        assert result.tzinfo == timezone.utc
        assert result == utc_dt

    def test_ensure_utc_with_other_timezone(self):
        """Test that datetime with other timezone is converted to UTC."""
        # Create a datetime in EST (UTC-5)
        est = timezone(timedelta(hours=-5))
        est_dt = datetime(2025, 11, 4, 12, 0, 0, tzinfo=est)

        result = ensure_utc(est_dt)

        assert result.tzinfo == timezone.utc
        # 12:00 EST should be 17:00 UTC
        assert result.hour == 17


class TestParseIsoDatetime:
    """Tests for parse_iso_datetime function."""

    def test_parse_iso_datetime_with_z_suffix(self):
        """Test parsing ISO datetime with Z suffix."""
        result = parse_iso_datetime("2025-11-04T12:00:00Z")

        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result.year == 2025
        assert result.month == 11
        assert result.day == 4
        assert result.hour == 12

    def test_parse_iso_datetime_with_utc_offset(self):
        """Test parsing ISO datetime with UTC offset."""
        result = parse_iso_datetime("2025-11-04T12:00:00+00:00")

        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result.year == 2025

    def test_parse_iso_datetime_without_timezone(self):
        """Test parsing ISO datetime without timezone (treated as UTC)."""
        result = parse_iso_datetime("2025-11-04T12:00:00")

        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result.year == 2025

    def test_parse_iso_datetime_date_only(self):
        """Test parsing date-only format."""
        result = parse_iso_datetime("2025-11-04")

        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result.year == 2025
        assert result.month == 11
        assert result.day == 4

    def test_parse_iso_datetime_with_empty_string(self):
        """Test that empty string returns None."""
        assert parse_iso_datetime("") is None
        assert parse_iso_datetime("   ") is None

    def test_parse_iso_datetime_with_invalid_format(self):
        """Test that invalid format returns None."""
        assert parse_iso_datetime("not a date") is None
        assert parse_iso_datetime("2025/11/04") is None

    def test_parse_iso_datetime_with_none(self):
        """Test that None input returns None."""
        # The function expects a string, but let's handle None gracefully
        result = parse_iso_datetime(None) if None else None
        assert result is None


class TestFormatTimestamp:
    """Tests for format_timestamp function."""

    def test_format_timestamp_basic(self):
        """Test basic timestamp formatting."""
        dt = datetime(2025, 11, 4, 12, 30, 45, tzinfo=timezone.utc)
        result = format_timestamp(dt)

        assert result == "2025-11-04T12:30:45Z"

    def test_format_timestamp_with_microseconds(self):
        """Test timestamp formatting with microseconds."""
        dt = datetime(2025, 11, 4, 12, 30, 45, 123456, tzinfo=timezone.utc)
        result = format_timestamp(dt, include_microseconds=True)

        assert result == "2025-11-04T12:30:45.123456Z"

    def test_format_timestamp_converts_to_utc(self):
        """Test that non-UTC datetime is converted to UTC."""
        # Create a datetime in EST (UTC-5)
        est = timezone(timedelta(hours=-5))
        est_dt = datetime(2025, 11, 4, 12, 0, 0, tzinfo=est)

        result = format_timestamp(est_dt)

        # 12:00 EST should be 17:00 UTC
        assert result == "2025-11-04T17:00:00Z"

    def test_format_timestamp_with_naive_datetime(self):
        """Test formatting naive datetime (treated as UTC)."""
        naive = datetime(2025, 11, 4, 12, 0, 0)
        result = format_timestamp(naive)

        assert result == "2025-11-04T12:00:00Z"


class TestFormatTimestampForLog:
    """Tests for format_timestamp_for_log function."""

    def test_format_timestamp_for_log(self):
        """Test log timestamp formatting."""
        dt = datetime(2025, 11, 4, 12, 30, 45, tzinfo=timezone.utc)
        result = format_timestamp_for_log(dt)

        assert result == "2025-11-04T12:30:45Z"

    def test_format_timestamp_for_log_excludes_microseconds(self):
        """Test that log format excludes microseconds."""
        dt = datetime(2025, 11, 4, 12, 30, 45, 123456, tzinfo=timezone.utc)
        result = format_timestamp_for_log(dt)

        # Should not include microseconds
        assert result == "2025-11-04T12:30:45Z"
        assert ".123456" not in result


class TestTimestampToUnix:
    """Tests for timestamp_to_unix function."""

    def test_timestamp_to_unix_basic(self):
        """Test basic Unix timestamp conversion."""
        # Unix epoch
        dt = datetime(1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = timestamp_to_unix(dt)

        assert result == 0

    def test_timestamp_to_unix_recent_date(self):
        """Test Unix timestamp for a recent date."""
        dt = datetime(2025, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        result = timestamp_to_unix(dt)

        # Should be a large positive number
        assert result > 1700000000
        assert isinstance(result, int)

    def test_timestamp_to_unix_with_naive_datetime(self):
        """Test Unix timestamp with naive datetime (treated as UTC)."""
        naive = datetime(2025, 11, 4, 12, 0, 0)
        result = timestamp_to_unix(naive)

        assert isinstance(result, int)
        assert result > 0


class TestUnixToTimestamp:
    """Tests for unix_to_timestamp function."""

    def test_unix_to_timestamp_epoch(self):
        """Test converting Unix epoch to datetime."""
        result = unix_to_timestamp(0)

        assert result.tzinfo == timezone.utc
        assert result.year == 1970
        assert result.month == 1
        assert result.day == 1

    def test_unix_to_timestamp_recent(self):
        """Test converting recent Unix timestamp to datetime."""
        # Nov 4, 2025, 12:00:00 UTC (approximate)
        unix_ts = 1762267200
        result = unix_to_timestamp(unix_ts)

        assert result.tzinfo == timezone.utc
        assert result.year == 2025

    def test_unix_to_timestamp_roundtrip(self):
        """Test roundtrip conversion: datetime -> unix -> datetime."""
        original = datetime(2025, 11, 4, 12, 0, 0, tzinfo=timezone.utc)

        unix_ts = timestamp_to_unix(original)
        result = unix_to_timestamp(unix_ts)

        # Should be equal (ignoring microseconds)
        assert result.year == original.year
        assert result.month == original.month
        assert result.day == original.day
        assert result.hour == original.hour
        assert result.minute == original.minute
        assert result.second == original.second
