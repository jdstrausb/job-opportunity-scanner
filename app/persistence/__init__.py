"""Persistence layer for database operations using SQLite.

This module provides the public API for database operations including:
- Database initialization and connection management
- Repository classes for CRUD operations on jobs, sources, and alerts
- Custom exceptions for error handling

Public API:
    # Database initialization and session management
    - init_database(database_url: str) -> None
    - get_session() -> ContextManager[Session]
    - close_database() -> None
    - get_engine() -> Engine

    # Repository classes
    - JobRepository: CRUD operations for jobs
    - SourceRepository: CRUD operations for source status tracking
    - AlertRepository: CRUD operations for alert records

    # Exceptions
    - PersistenceError: Base exception for all persistence errors
    - DatabaseConnectionError: Database connection/initialization failures
    - RecordNotFoundError: Required record not found
    - DataIntegrityError: Constraint violations

Example usage:
    >>> from app.persistence import init_database, get_session, JobRepository
    >>> from app.domain.models import Job
    >>>
    >>> # Initialize database (once at startup)
    >>> init_database("sqlite:///./data/job_scanner.db")
    >>>
    >>> # Use repository within session context
    >>> with get_session() as session:
    ...     repo = JobRepository(session)
    ...     job = repo.get_by_key("abc123")
"""

# Database initialization and session management
from .database import close_database, get_engine, get_session, init_database

# Repository classes
from .repositories import AlertRepository, JobRepository, SourceRepository

# Exceptions
from .exceptions import (
    DatabaseConnectionError,
    DataIntegrityError,
    PersistenceError,
    RecordNotFoundError,
)

# Public API exports
__all__ = [
    # Database functions
    "init_database",
    "get_session",
    "close_database",
    "get_engine",
    # Repositories
    "JobRepository",
    "SourceRepository",
    "AlertRepository",
    # Exceptions
    "PersistenceError",
    "DatabaseConnectionError",
    "RecordNotFoundError",
    "DataIntegrityError",
]
