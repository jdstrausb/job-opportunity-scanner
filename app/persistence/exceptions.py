"""Persistence layer exceptions.

This module defines custom exceptions for database and persistence operations.
All persistence exceptions inherit from PersistenceError for easy catching.
"""


class PersistenceError(Exception):
    """Base exception for all persistence layer errors.

    All database-related exceptions should inherit from this class.
    This allows callers to catch all persistence errors with a single except clause.
    """

    pass


class DatabaseConnectionError(PersistenceError):
    """Raised when database connection or initialization fails.

    Examples:
    - Invalid database URL format
    - Database file not accessible
    - Database file permissions incorrect
    - SQLite driver not available
    """

    pass


class RecordNotFoundError(PersistenceError):
    """Raised when a required database record is not found.

    This is used for operations that expect a record to exist.
    For optional lookups, methods should return None instead of raising this.
    """

    pass


class DataIntegrityError(PersistenceError):
    """Raised when database constraint violation occurs.

    Examples:
    - Primary key violation
    - Unique constraint violation
    - Foreign key constraint violation (if enabled)
    - Check constraint violation
    """

    pass
