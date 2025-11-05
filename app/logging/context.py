"""Context propagation for structured logging.

This module provides utilities for maintaining contextual metadata that is
automatically injected into all log records within a scope. Context is thread-safe
and uses Python's contextvars for proper async support.
"""

from contextvars import ContextVar, Token
from typing import Any, Dict, Optional


# Context variable to store logging context across call chains
LogContextVar: ContextVar[Dict[str, Any]] = ContextVar("log_context", default={})


def get_log_context() -> Dict[str, Any]:
    """Get the current logging context.

    Returns:
        Dictionary of current context fields
    """
    return LogContextVar.get().copy()


def push_log_context(**kwargs) -> Token:
    """Push new context fields onto the logging context stack.

    This merges new fields with existing context. Use pop_log_context()
    to restore the previous state.

    Args:
        **kwargs: Key-value pairs to add to the logging context

    Returns:
        Token that can be used to restore previous context state

    Example:
        >>> token = push_log_context(run_id="abc123", source_id="acme-corp")
        >>> # ... do work, all logs will include run_id and source_id ...
        >>> pop_log_context(token)
    """
    current = LogContextVar.get()
    new_context = {**current, **kwargs}
    return LogContextVar.set(new_context)


def pop_log_context(token: Token) -> None:
    """Restore the logging context to a previous state.

    Args:
        token: Token returned from push_log_context()

    Example:
        >>> token = push_log_context(run_id="abc123")
        >>> # ... do work ...
        >>> pop_log_context(token)
    """
    LogContextVar.reset(token)


def clear_log_context() -> None:
    """Clear all logging context fields.

    This is primarily useful for testing.
    """
    LogContextVar.set({})


class log_context:
    """Context manager for scoped logging context.

    Automatically pushes context on entry and pops on exit, even if
    an exception occurs.

    Example:
        >>> with log_context(run_id="abc123", source_id="acme-corp"):
        ...     logger.info("Processing source")  # includes run_id and source_id
        ...     # ... do work ...
        ... # context automatically restored on exit
    """

    def __init__(self, **kwargs):
        """Initialize context manager with fields to add.

        Args:
            **kwargs: Key-value pairs to add to the logging context
        """
        self.kwargs = kwargs
        self.token: Optional[Token] = None

    def __enter__(self):
        """Enter the context manager, pushing new context fields."""
        self.token = push_log_context(**self.kwargs)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context manager, restoring previous context."""
        if self.token is not None:
            pop_log_context(self.token)
        return False  # Don't suppress exceptions
