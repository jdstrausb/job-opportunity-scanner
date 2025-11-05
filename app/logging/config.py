"""Logging configuration for the job opportunity scanner."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Literal

from .context import get_log_context

LogFormat = Literal["json", "key-value"]


class ContextualFilter(logging.Filter):
    """Filter that enriches log records with static metadata and active context.

    This filter merges:
    1. Static fields (service, environment) into every record
    2. Active context from LogContextVar (run_id, source_id, job_key, etc.)
    3. Any additional 'extra' fields passed to the log call
    """

    def __init__(self, service: str = "job-opportunity-scanner", environment: str = "local"):
        """Initialize contextual filter.

        Args:
            service: Service name (static field)
            environment: Environment label (production, staging, local)
        """
        super().__init__()
        self.service = service
        self.environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        """Enrich record with static metadata and active context.

        Args:
            record: Log record to enrich

        Returns:
            True (always allow record to pass)
        """
        # Add static fields
        record.service = self.service
        record.environment = self.environment

        # Merge active context from contextvars
        context = get_log_context()
        for key, value in context.items():
            if not hasattr(record, key):
                setattr(record, key, value)

        return True


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging.

    Produces single-line JSON objects with stable field names.
    Automatically includes all extra fields and context variables.
    """

    # Standard log record attributes to exclude from extras
    STANDARD_ATTRS = {
        "name", "msg", "args", "created", "filename", "funcName", "levelname",
        "levelno", "lineno", "module", "msecs", "message", "pathname", "process",
        "processName", "relativeCreated", "thread", "threadName", "asctime",
        "exc_info", "exc_text", "stack_info", "taskName"
    }

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.

        Args:
            record: Log record to format

        Returns:
            JSON string with all fields
        """
        # Build base log object with mandatory fields
        log_obj: Dict[str, Any] = {
            "timestamp": self._format_timestamp(record.created),
            "level": record.levelname,
            "message": record.getMessage(),
        }

        # Add all extra fields (including static and context fields)
        for key, value in record.__dict__.items():
            if key not in self.STANDARD_ATTRS and not key.startswith("_"):
                # Handle special types
                if isinstance(value, (datetime,)):
                    log_obj[key] = value.isoformat()
                elif isinstance(value, (str, int, float, bool, type(None))):
                    log_obj[key] = value
                elif isinstance(value, (list, dict)):
                    log_obj[key] = value
                else:
                    # Convert other types to string
                    log_obj[key] = str(value)

        # Add exception info if present
        if record.exc_info:
            log_obj["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, ensure_ascii=False)

    def _format_timestamp(self, created: float) -> str:
        """Format timestamp as ISO-8601 UTC.

        Args:
            created: Unix timestamp (seconds since epoch)

        Returns:
            ISO-8601 formatted timestamp with 'Z' suffix
        """
        dt = datetime.fromtimestamp(created, tz=timezone.utc)
        # Format as ISO-8601 with 'Z' suffix (e.g., 2025-11-04T10:30:00.123456Z)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class KeyValueFormatter(logging.Formatter):
    """Key-value formatter for human-readable logs.

    Produces logs in format:
    timestamp [level] component: message key1=value1 key2=value2
    """

    # Standard attributes to skip in key-value output
    SKIP_ATTRS = {
        "name", "msg", "args", "created", "filename", "funcName", "levelname",
        "levelno", "lineno", "module", "msecs", "message", "pathname", "process",
        "processName", "relativeCreated", "thread", "threadName", "asctime",
        "exc_info", "exc_text", "stack_info", "taskName", "service", "environment"
    }

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as key-value pairs.

        Args:
            record: Log record to format

        Returns:
            Human-readable log line with key=value pairs
        """
        # Base format: timestamp [level] name: message
        base = super().format(record)

        # Collect extra fields
        extras = []
        for key, value in sorted(record.__dict__.items()):
            if key not in self.SKIP_ATTRS and not key.startswith("_"):
                # Format value
                if isinstance(value, str):
                    # Quote strings with spaces or special chars
                    if " " in value or "=" in value or "," in value:
                        value_str = f'"{value}"'
                    else:
                        value_str = value
                elif isinstance(value, (datetime,)):
                    value_str = value.isoformat()
                elif isinstance(value, bool):
                    value_str = str(value).lower()
                elif value is None:
                    value_str = "null"
                else:
                    value_str = str(value)

                extras.append(f"{key}={value_str}")

        if extras:
            return f"{base} {' '.join(extras)}"
        else:
            return base


def configure_logging(
    level: str = "INFO",
    format_type: LogFormat = "key-value",
    environment: str = "local",
) -> None:
    """
    Configure the root logger with the specified level and format.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_type: Output format - 'json' for JSON logs or 'key-value' for human-readable
        environment: Environment label (production, staging, local)

    Raises:
        ValueError: If level or format_type is invalid
    """
    # Validate level
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")

    # Validate format type
    if format_type not in ("json", "key-value"):
        raise ValueError(f"Invalid log format: {format_type}. Must be 'json' or 'key-value'")

    # Create handler with appropriate formatter
    handler = logging.StreamHandler(sys.stdout)

    if format_type == "json":
        formatter = JSONFormatter()
    else:
        # Key-value format with timestamp
        formatter = KeyValueFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)

    # Add contextual filter to enrich all records
    contextual_filter = ContextualFilter(
        service="job-opportunity-scanner",
        environment=environment,
    )
    handler.addFilter(contextual_filter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Add our handler
    root_logger.addHandler(handler)

    # Log the configuration (using the new structured format)
    logger = logging.getLogger(__name__)
    logger.info(
        "Logging configured",
        extra={
            "event": "logging.configured",
            "component": "logging",
            "log_level": level.upper(),
            "log_format": format_type,
        },
    )
