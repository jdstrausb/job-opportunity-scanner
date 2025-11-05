"""Logging and observability configuration for structured event emission."""

import logging
from typing import Any, Dict, Optional


class ComponentLoggerAdapter(logging.LoggerAdapter):
    """LoggerAdapter that properly merges component with extra fields."""

    def process(self, msg, kwargs):
        """Process log call, merging adapter extra with call extra."""
        # Get existing extra from kwargs, or create empty dict
        extra = kwargs.get('extra', {})

        # Merge adapter's extra (component) with call's extra
        # Call's extra takes precedence
        merged_extra = {**self.extra, **extra}

        # Update kwargs with merged extra
        kwargs['extra'] = merged_extra

        return msg, kwargs


def get_logger(name: str, component: Optional[str] = None):
    """Get a logger with optional default component field.

    This is a convenience wrapper around logging.getLogger() that allows
    you to specify a default component field that will be included in
    all log records from this logger.

    Args:
        name: Logger name (typically __name__)
        component: Optional component identifier to inject into all logs

    Returns:
        Logger or ComponentLoggerAdapter instance

    Example:
        >>> logger = get_logger(__name__, component="pipeline")
        >>> logger.info("Processing started", extra={"event": "pipeline.run.started"})
    """
    logger = logging.getLogger(name)

    if component:
        # Return a LoggerAdapter that injects component into all records
        return ComponentLoggerAdapter(logger, {"component": component})

    return logger
