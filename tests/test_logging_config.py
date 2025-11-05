"""Tests for logging configuration and formatters."""

import json
import logging

import pytest

from app.logging.config import (
    ContextualFilter,
    JSONFormatter,
    KeyValueFormatter,
    configure_logging,
)
from app.logging.context import clear_log_context, log_context


@pytest.fixture(autouse=True)
def clean_context():
    """Clear logging context before and after each test."""
    clear_log_context()
    yield
    clear_log_context()


@pytest.fixture
def logger():
    """Create a test logger with handler for capturing output."""
    # Create logger
    test_logger = logging.getLogger("test_logger")
    test_logger.setLevel(logging.DEBUG)
    test_logger.handlers.clear()

    yield test_logger

    # Cleanup
    test_logger.handlers.clear()


def test_json_formatter_basic(logger):
    """Test JSONFormatter produces valid JSON with mandatory fields."""
    formatter = JSONFormatter()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    # Create log record
    record = logger.makeRecord(
        "test", logging.INFO, "test.py", 1, "Test message", (), None
    )

    # Format record
    output = formatter.format(record)

    # Parse JSON
    log_obj = json.loads(output)

    # Check mandatory fields
    assert "timestamp" in log_obj
    assert "level" in log_obj
    assert "message" in log_obj
    assert log_obj["level"] == "INFO"
    assert log_obj["message"] == "Test message"


def test_json_formatter_with_extra_fields(logger):
    """Test JSONFormatter includes extra fields."""
    formatter = JSONFormatter()

    # Create record with extra fields
    record = logger.makeRecord(
        "test",
        logging.INFO,
        "test.py",
        1,
        "Test message",
        (),
        None,
        extra={"event": "test.event", "count": 42, "flag": True},
    )

    output = formatter.format(record)
    log_obj = json.loads(output)

    assert log_obj["event"] == "test.event"
    assert log_obj["count"] == 42
    assert log_obj["flag"] is True


def test_contextual_filter_adds_static_fields(logger):
    """Test ContextualFilter adds static service and environment fields."""
    filter = ContextualFilter(service="test-service", environment="test")

    record = logger.makeRecord(
        "test", logging.INFO, "test.py", 1, "Test message", (), None
    )

    # Apply filter
    filter.filter(record)

    # Check static fields were added
    assert hasattr(record, "service")
    assert hasattr(record, "environment")
    assert record.service == "test-service"
    assert record.environment == "test"


def test_contextual_filter_adds_context_fields(logger):
    """Test ContextualFilter adds fields from log context."""
    filter = ContextualFilter()

    # Set context
    with log_context(run_id="abc123", source_id="acme-corp"):
        record = logger.makeRecord(
            "test", logging.INFO, "test.py", 1, "Test message", (), None
        )

        # Apply filter
        filter.filter(record)

        # Check context fields were added
        assert hasattr(record, "run_id")
        assert hasattr(record, "source_id")
        assert record.run_id == "abc123"
        assert record.source_id == "acme-corp"


def test_json_formatter_with_context(logger):
    """Test full pipeline: context + filter + JSON formatter."""
    # Setup
    formatter = JSONFormatter()
    filter = ContextualFilter(service="job-scanner", environment="test")

    with log_context(run_id="abc123", source_id="acme-corp"):
        # Create record
        record = logger.makeRecord(
            "test",
            logging.INFO,
            "test.py",
            1,
            "Processing source",
            (),
            None,
            extra={"event": "source.run.started"},
        )

        # Apply filter and format
        filter.filter(record)
        output = formatter.format(record)

        # Parse and verify
        log_obj = json.loads(output)
        assert log_obj["message"] == "Processing source"
        assert log_obj["event"] == "source.run.started"
        assert log_obj["service"] == "job-scanner"
        assert log_obj["environment"] == "test"
        assert log_obj["run_id"] == "abc123"
        assert log_obj["source_id"] == "acme-corp"


def test_key_value_formatter_basic(logger):
    """Test KeyValueFormatter produces readable output."""
    formatter = KeyValueFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    record = logger.makeRecord(
        "test", logging.INFO, "test.py", 1, "Test message", (), None
    )

    output = formatter.format(record)

    # Should contain basic parts
    assert "[INFO]" in output
    assert "test" in output
    assert "Test message" in output


def test_key_value_formatter_with_extras(logger):
    """Test KeyValueFormatter includes extra fields as key=value pairs."""
    formatter = KeyValueFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    record = logger.makeRecord(
        "test",
        logging.INFO,
        "test.py",
        1,
        "Test message",
        (),
        None,
        extra={"event": "test.event", "count": 42},
    )

    output = formatter.format(record)

    # Should contain key=value pairs
    assert "event=test.event" in output
    assert "count=42" in output


def test_configure_logging_invalid_level():
    """Test configure_logging rejects invalid log level."""
    with pytest.raises(ValueError, match="Invalid log level"):
        configure_logging(level="INVALID")


def test_configure_logging_invalid_format():
    """Test configure_logging rejects invalid format type."""
    with pytest.raises(ValueError, match="Invalid log format"):
        configure_logging(format_type="invalid")


def test_configure_logging_json_format(caplog):
    """Test configure_logging with JSON format."""
    configure_logging(level="INFO", format_type="json", environment="test")

    # Get the root logger
    root_logger = logging.getLogger()

    # Verify it has handlers
    assert len(root_logger.handlers) > 0

    # Verify the formatter is JSONFormatter
    handler = root_logger.handlers[0]
    assert isinstance(handler.formatter, JSONFormatter)


def test_configure_logging_key_value_format(caplog):
    """Test configure_logging with key-value format."""
    configure_logging(level="INFO", format_type="key-value", environment="test")

    # Get the root logger
    root_logger = logging.getLogger()

    # Verify it has handlers
    assert len(root_logger.handlers) > 0

    # Verify the formatter is KeyValueFormatter
    handler = root_logger.handlers[0]
    assert isinstance(handler.formatter, KeyValueFormatter)


def test_timestamp_format_in_json(logger):
    """Test that JSON formatter produces correct ISO-8601 timestamp format."""
    formatter = JSONFormatter()

    record = logger.makeRecord(
        "test", logging.INFO, "test.py", 1, "Test message", (), None
    )

    output = formatter.format(record)
    log_obj = json.loads(output)

    # Timestamp should be ISO-8601 with Z suffix
    timestamp = log_obj["timestamp"]
    assert timestamp.endswith("Z")
    assert "T" in timestamp
    # Should match pattern: YYYY-MM-DDTHH:MM:SS.sssZ
    assert len(timestamp) == 24  # 2025-11-04T10:30:00.123Z


def test_json_formatter_no_duplicate_fields(logger):
    """Test that JSON formatter doesn't duplicate standard fields in extras."""
    formatter = JSONFormatter()

    record = logger.makeRecord(
        "test",
        logging.INFO,
        "test.py",
        1,
        "Test message",
        (),
        None,
        extra={"event": "test.event"},
    )

    output = formatter.format(record)
    log_obj = json.loads(output)

    # Standard fields should appear only once
    assert "message" in log_obj
    assert "name" not in log_obj  # name is a standard field, shouldn't be in output
    assert "event" in log_obj
