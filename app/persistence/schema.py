"""Database schema definition and ORM models.

This module defines SQLAlchemy ORM models for the database schema and provides
conversion methods between ORM models and domain models.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Index, MetaData, String, Text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base

from app.domain.models import AlertRecord, Job, SourceStatus

logger = logging.getLogger(__name__)

# Create base class for ORM models
Base = declarative_base()
metadata = MetaData()


class JobModel(Base):
    """ORM model for jobs table.

    Stores normalized job postings with tracking metadata.
    """

    __tablename__ = "jobs"

    # Primary key
    job_key = Column(String(64), primary_key=True, nullable=False)

    # Source information
    source_type = Column(String(50), nullable=False)
    source_identifier = Column(String(255), nullable=False)
    external_id = Column(String(255), nullable=False)

    # Job details
    title = Column(Text, nullable=False)
    company = Column(String(255), nullable=False)
    location = Column(String(255), nullable=True)
    description = Column(Text, nullable=False)
    url = Column(Text, nullable=False)

    # Timestamps (stored as ISO 8601 strings)
    posted_at = Column(String(50), nullable=True)
    updated_at = Column(String(50), nullable=True)
    first_seen_at = Column(String(50), nullable=False)
    last_seen_at = Column(String(50), nullable=False)

    # Change detection
    content_hash = Column(String(64), nullable=False)

    # Indexes
    __table_args__ = (
        Index("idx_jobs_source", "source_type", "source_identifier"),
        Index("idx_jobs_last_seen", "last_seen_at"),
        Index("idx_jobs_content_hash", "content_hash"),
    )

    def to_domain(self) -> Job:
        """Convert ORM model to domain model.

        Returns:
            Job: Domain model instance
        """
        return Job(
            job_key=self.job_key,
            source_type=self.source_type,
            source_identifier=self.source_identifier,
            external_id=self.external_id,
            title=self.title,
            company=self.company,
            location=self.location,
            description=self.description,
            url=self.url,
            posted_at=_parse_datetime(self.posted_at),
            updated_at=_parse_datetime(self.updated_at),
            first_seen_at=_parse_datetime(self.first_seen_at),
            last_seen_at=_parse_datetime(self.last_seen_at),
            content_hash=self.content_hash,
        )

    @classmethod
    def from_domain(cls, job: Job) -> "JobModel":
        """Create ORM model from domain model.

        Args:
            job: Domain model instance

        Returns:
            JobModel: ORM model instance
        """
        return cls(
            job_key=job.job_key,
            source_type=job.source_type,
            source_identifier=job.source_identifier,
            external_id=job.external_id,
            title=job.title,
            company=job.company,
            location=job.location,
            description=job.description,
            url=job.url,
            posted_at=_format_datetime(job.posted_at),
            updated_at=_format_datetime(job.updated_at),
            first_seen_at=_format_datetime(job.first_seen_at),
            last_seen_at=_format_datetime(job.last_seen_at),
            content_hash=job.content_hash,
        )


class SourceStatusModel(Base):
    """ORM model for sources table.

    Tracks source health and status for observability.
    """

    __tablename__ = "sources"

    # Primary key
    source_identifier = Column(String(255), primary_key=True, nullable=False)

    # Source information
    name = Column(String(255), nullable=False)
    source_type = Column(String(50), nullable=False)

    # Health tracking (timestamps stored as ISO 8601 strings)
    last_success_at = Column(String(50), nullable=True)
    last_error_at = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)

    def to_domain(self) -> SourceStatus:
        """Convert ORM model to domain model.

        Returns:
            SourceStatus: Domain model instance
        """
        return SourceStatus(
            source_identifier=self.source_identifier,
            name=self.name,
            source_type=self.source_type,
            last_success_at=_parse_datetime(self.last_success_at),
            last_error_at=_parse_datetime(self.last_error_at),
            error_message=self.error_message,
        )

    @classmethod
    def from_domain(cls, source_status: SourceStatus) -> "SourceStatusModel":
        """Create ORM model from domain model.

        Args:
            source_status: Domain model instance

        Returns:
            SourceStatusModel: ORM model instance
        """
        return cls(
            source_identifier=source_status.source_identifier,
            name=source_status.name,
            source_type=source_status.source_type,
            last_success_at=_format_datetime(source_status.last_success_at),
            last_error_at=_format_datetime(source_status.last_error_at),
            error_message=source_status.error_message,
        )


class AlertRecordModel(Base):
    """ORM model for alerts_sent table.

    Tracks which job versions have been alerted to prevent duplicate notifications.
    """

    __tablename__ = "alerts_sent"

    # Composite primary key
    job_key = Column(String(64), primary_key=True, nullable=False)
    version_hash = Column(String(64), primary_key=True, nullable=False)

    # Timestamp (stored as ISO 8601 string)
    sent_at = Column(String(50), nullable=False)

    # Indexes
    __table_args__ = (Index("idx_alerts_sent_at", "sent_at"),)

    def to_domain(self) -> AlertRecord:
        """Convert ORM model to domain model.

        Returns:
            AlertRecord: Domain model instance
        """
        return AlertRecord(
            job_key=self.job_key,
            version_hash=self.version_hash,
            sent_at=_parse_datetime(self.sent_at),
        )

    @classmethod
    def from_domain(cls, alert_record: AlertRecord) -> "AlertRecordModel":
        """Create ORM model from domain model.

        Args:
            alert_record: Domain model instance

        Returns:
            AlertRecordModel: ORM model instance
        """
        return cls(
            job_key=alert_record.job_key,
            version_hash=alert_record.version_hash,
            sent_at=_format_datetime(alert_record.sent_at),
        )


def _format_datetime(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime as ISO 8601 string for database storage.

    Args:
        dt: Datetime object (must be timezone-aware UTC)

    Returns:
        ISO 8601 formatted string or None
    """
    if dt is None:
        return None

    # Ensure datetime is UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Format as ISO 8601 with explicit Z suffix
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 string to datetime object.

    Args:
        dt_str: ISO 8601 formatted string

    Returns:
        Timezone-aware datetime in UTC or None
    """
    if dt_str is None or dt_str == "":
        return None

    # Parse ISO 8601 format
    # Handle formats: YYYY-MM-DDTHH:MM:SS.ffffffZ or YYYY-MM-DDTHH:MM:SSZ
    dt_str = dt_str.rstrip("Z")  # Remove Z suffix if present

    # Try with microseconds first
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        # Try without microseconds
        dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")

    # Ensure timezone-aware UTC
    return dt.replace(tzinfo=timezone.utc)


def create_schema(engine: Engine) -> None:
    """Create all tables and indexes if they don't exist (idempotent).

    This function uses SQLAlchemy's metadata.create_all() with checkfirst=True,
    which makes it safe to call multiple times.

    Args:
        engine: SQLAlchemy engine instance
    """
    logger.info("Creating database schema if not exists")

    try:
        # Create all tables defined in Base
        Base.metadata.create_all(engine, checkfirst=True)

        # Log created tables
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        logger.info(f"Database schema ready. Tables: {', '.join(tables)}")

    except Exception as e:
        logger.error(f"Failed to create database schema: {e}", exc_info=True)
        raise
