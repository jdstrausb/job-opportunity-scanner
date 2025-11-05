"""Database connection and session management.

This module provides database initialization, engine creation, and session lifecycle
management for the persistence layer.
"""

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.logging import get_logger

from .exceptions import DatabaseConnectionError

# Module-level engine and session factory
_engine: Engine | None = None
_session_factory: sessionmaker | None = None

logger = get_logger(__name__, component="database")


def init_database(database_url: str) -> None:
    """Initialize database connection and create schema if tables don't exist.

    This function should be called once during application startup. It:
    1. Creates SQLAlchemy engine with appropriate configuration
    2. Configures SQLite-specific settings (foreign keys, timeouts)
    3. Validates the connection
    4. Creates database schema if tables don't exist

    Args:
        database_url: Database connection URL (e.g., "sqlite:///./data/job_scanner.db")

    Raises:
        DatabaseConnectionError: If database initialization fails

    Example:
        >>> init_database("sqlite:///./data/job_scanner.db")
    """
    global _engine, _session_factory

    try:
        logger.info(
            f"Initializing database",
            extra={
                "event": "database.initializing",
                "database_url": _redact_url(database_url),
            }
        )

        # Validate URL format
        if not database_url or not isinstance(database_url, str):
            raise DatabaseConnectionError("Database URL must be a non-empty string")

        # For SQLite file databases, ensure parent directory exists
        if database_url.startswith("sqlite:///") and not database_url.endswith(":memory:"):
            # Extract file path from URL (remove "sqlite:///" prefix)
            db_path = database_url.replace("sqlite:///", "")
            db_file = Path(db_path)

            # Create parent directory if it doesn't exist
            if not db_file.parent.exists():
                logger.info(f"Creating database directory: {db_file.parent}")
                db_file.parent.mkdir(parents=True, exist_ok=True)

        # Create engine with appropriate configuration
        _engine = create_engine(
            database_url,
            echo=False,  # Set to True for SQL debugging (controlled by LOG_LEVEL)
            pool_pre_ping=True,  # Verify connections before use
            future=True,  # Use SQLAlchemy 2.0 API
            # SQLite-specific connection arguments
            connect_args={
                "check_same_thread": False,  # Allow multi-threaded access (needed for tests)
                "timeout": 30,  # Wait up to 30 seconds for locks
            }
            if database_url.startswith("sqlite")
            else {},
        )

        # Enable foreign keys for SQLite
        if database_url.startswith("sqlite"):
            _configure_sqlite(_engine)

        # Validate connection
        _validate_connection(_engine)

        # Create session factory
        _session_factory = sessionmaker(
            bind=_engine,
            autocommit=False,  # Use explicit transactions
            autoflush=True,  # Flush changes before queries
            expire_on_commit=False,  # Keep objects accessible after commit
            future=True,  # Use SQLAlchemy 2.0 API
        )

        # Create schema if needed
        from .schema import create_schema

        create_schema(_engine)

        logger.info(
            "Database initialized successfully",
            extra={
                "event": "database.initialised",
                "database_url": _redact_url(database_url),
            }
        )

    except DatabaseConnectionError:
        raise
    except Exception as e:
        error_msg = f"Failed to initialize database: {e}"
        logger.error(error_msg, exc_info=True)
        raise DatabaseConnectionError(error_msg) from e


def _configure_sqlite(engine: Engine) -> None:
    """Configure SQLite-specific settings.

    Args:
        engine: SQLAlchemy engine instance
    """

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        """Enable foreign keys and other SQLite pragmas on connection."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging for better concurrency
        cursor.close()


def _validate_connection(engine: Engine) -> None:
    """Validate database connection by executing a test query.

    Args:
        engine: SQLAlchemy engine instance

    Raises:
        DatabaseConnectionError: If connection test fails
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            result.fetchone()
        logger.debug("Database connection validated successfully")
    except Exception as e:
        raise DatabaseConnectionError(f"Failed to validate database connection: {e}") from e


def _redact_url(url: str) -> str:
    """Redact sensitive information from database URL for logging.

    Args:
        url: Database connection URL

    Returns:
        Redacted URL safe for logging
    """
    # For SQLite file paths, show the URL
    if url.startswith("sqlite"):
        return url

    # For other databases, redact password if present
    # Format: dialect://username:password@host:port/database
    if "@" in url and ":" in url:
        parts = url.split("@")
        if len(parts) == 2:
            prefix = parts[0].split(":")[0]  # Keep dialect://username
            return f"{prefix}:***@{parts[1]}"

    return url


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Provide a database session with automatic transaction management.

    This context manager:
    1. Creates a new session from the session factory
    2. Yields the session for use
    3. Commits the transaction on successful exit
    4. Rolls back the transaction on exception
    5. Closes the session in all cases

    Yields:
        Session: SQLAlchemy session for database operations

    Raises:
        DatabaseConnectionError: If database not initialized or session creation fails
        Exception: Any exception from operations within the context

    Example:
        >>> with get_session() as session:
        ...     repo = JobRepository(session)
        ...     job = repo.get_by_key("abc123")
    """
    global _session_factory

    if _session_factory is None:
        raise DatabaseConnectionError(
            "Database not initialized. Call init_database() before using get_session()"
        )

    session = _session_factory()
    try:
        yield session
        session.commit()
        logger.debug(
            "Database session committed",
            extra={"event": "database.session.committed"}
        )
    except Exception as e:
        session.rollback()
        logger.warning(
            f"Database session rolled back due to exception: {e}",
            extra={
                "event": "database.session.rolled_back",
                "error_type": type(e).__name__,
            }
        )
        raise
    finally:
        session.close()


def get_engine() -> Engine:
    """Get the database engine instance.

    Returns:
        Engine: SQLAlchemy engine

    Raises:
        DatabaseConnectionError: If database not initialized
    """
    global _engine

    if _engine is None:
        raise DatabaseConnectionError(
            "Database not initialized. Call init_database() before using get_engine()"
        )

    return _engine


def close_database() -> None:
    """Close database connections and cleanup resources.

    This should be called during application shutdown.
    """
    global _engine, _session_factory

    if _engine is not None:
        logger.info("Closing database connections")
        _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connections closed")
