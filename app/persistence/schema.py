"""Database schema definition and ORM models.

This module defines SQLAlchemy ORM models for the database schema and provides
conversion methods between ORM models and domain models.

Timestamps are exposed to Python as timezone-aware UTC ``datetime`` objects and
stored as fixed-width ISO 8601 strings (``YYYY-MM-DDTHH:MM:SS.ffffffZ``) by the
``UtcIsoString`` column type. Because the stored strings sort lexically in time
order, comparisons such as ``JobModel.last_seen_at < cutoff`` work directly with
``datetime`` operands.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Dialect, Index, String, Text, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.domain.models import AlertRecord, DismissalScope, DismissedItem, Job, SourceStatus
from app.utils.timestamps import ensure_utc

logger = logging.getLogger(__name__)

# On-disk timestamp format. Changing it breaks string comparisons against
# rows written by earlier versions, so treat it as part of the schema.
_STORAGE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class UtcIsoString(TypeDecorator[datetime]):
    """Column type mapping UTC ``datetime`` values to ISO 8601 strings.

    Naive datetimes are treated as UTC; aware datetimes are converted to UTC.
    Reads accept values with or without microseconds and with or without the
    trailing ``Z`` so rows written by older versions still load.
    """

    impl = String(50)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            msg = f"UtcIsoString expects datetime, got {type(value).__name__}"
            raise TypeError(msg)
        utc_value = ensure_utc(value)
        assert utc_value is not None
        return utc_value.strftime(_STORAGE_FORMAT)

    def process_result_value(self, value: Any | None, dialect: Dialect) -> datetime | None:
        if value is None or value == "":
            return None
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class JobModel(Base):
    """ORM model for jobs table.

    Stores normalized job postings with tracking metadata.
    """

    __tablename__ = "jobs"

    # Primary key
    job_key: Mapped[str] = mapped_column(String(64), primary_key=True)

    # Source information
    source_type: Mapped[str] = mapped_column(String(50))
    source_identifier: Mapped[str] = mapped_column(String(255))
    external_id: Mapped[str] = mapped_column(String(255))

    # Job details
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)

    # Timestamps
    posted_at: Mapped[datetime | None] = mapped_column(UtcIsoString)
    updated_at: Mapped[datetime | None] = mapped_column(UtcIsoString)
    first_seen_at: Mapped[datetime] = mapped_column(UtcIsoString)
    last_seen_at: Mapped[datetime] = mapped_column(UtcIsoString)

    # Change detection
    content_hash: Mapped[str] = mapped_column(String(64))

    # Location metadata for filtering
    secondary_locations: Mapped[list[str] | None] = mapped_column(JSON)
    is_remote: Mapped[bool | None] = mapped_column(Boolean)
    location_country: Mapped[str | None] = mapped_column(String(255))

    # Indexes
    __table_args__ = (
        Index("idx_jobs_source", "source_type", "source_identifier"),
        Index("idx_jobs_last_seen", "last_seen_at"),
        Index("idx_jobs_content_hash", "content_hash"),
    )

    def to_domain(self) -> Job:
        """Convert ORM model to domain model."""
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
            posted_at=self.posted_at,
            updated_at=self.updated_at,
            first_seen_at=self.first_seen_at,
            last_seen_at=self.last_seen_at,
            content_hash=self.content_hash,
            secondary_locations=self.secondary_locations or [],
            is_remote=self.is_remote,
            location_country=self.location_country,
        )

    @classmethod
    def from_domain(cls, job: Job) -> "JobModel":
        """Create ORM model from domain model."""
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
            posted_at=job.posted_at,
            updated_at=job.updated_at,
            first_seen_at=job.first_seen_at,
            last_seen_at=job.last_seen_at,
            content_hash=job.content_hash,
            secondary_locations=job.secondary_locations or None,
            is_remote=job.is_remote,
            location_country=job.location_country,
        )


class SourceStatusModel(Base):
    """ORM model for sources table.

    Tracks source health and status for observability.
    """

    __tablename__ = "sources"

    # Primary key
    source_identifier: Mapped[str] = mapped_column(String(255), primary_key=True)

    # Source information
    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(50))

    # Health tracking
    last_success_at: Mapped[datetime | None] = mapped_column(UtcIsoString)
    last_error_at: Mapped[datetime | None] = mapped_column(UtcIsoString)
    error_message: Mapped[str | None] = mapped_column(Text)

    def to_domain(self) -> SourceStatus:
        """Convert ORM model to domain model."""
        return SourceStatus(
            source_identifier=self.source_identifier,
            name=self.name,
            source_type=self.source_type,
            last_success_at=self.last_success_at,
            last_error_at=self.last_error_at,
            error_message=self.error_message,
        )

    @classmethod
    def from_domain(cls, source_status: SourceStatus) -> "SourceStatusModel":
        """Create ORM model from domain model."""
        return cls(
            source_identifier=source_status.source_identifier,
            name=source_status.name,
            source_type=source_status.source_type,
            last_success_at=source_status.last_success_at,
            last_error_at=source_status.last_error_at,
            error_message=source_status.error_message,
        )


class AlertRecordModel(Base):
    """ORM model for alerts_sent table.

    Tracks which job versions have been alerted to prevent duplicate notifications.
    """

    __tablename__ = "alerts_sent"

    # Composite primary key
    job_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_hash: Mapped[str] = mapped_column(String(64), primary_key=True)

    sent_at: Mapped[datetime] = mapped_column(UtcIsoString)

    # Indexes
    __table_args__ = (Index("idx_alerts_sent_at", "sent_at"),)

    def to_domain(self) -> AlertRecord:
        """Convert ORM model to domain model."""
        return AlertRecord(
            job_key=self.job_key,
            version_hash=self.version_hash,
            sent_at=self.sent_at,
        )

    @classmethod
    def from_domain(cls, alert_record: AlertRecord) -> "AlertRecordModel":
        """Create ORM model from domain model."""
        return cls(
            job_key=alert_record.job_key,
            version_hash=alert_record.version_hash,
            sent_at=alert_record.sent_at,
        )


class DismissedItemModel(Base):
    """ORM model for dismissed_items table.

    Stores persistent job, company, and source mutes.
    """

    __tablename__ = "dismissed_items"

    # Composite primary key
    item_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    scope: Mapped[str] = mapped_column(String(50), primary_key=True)

    reason: Mapped[str | None] = mapped_column(Text)
    dismissed_at: Mapped[datetime] = mapped_column(UtcIsoString)

    # Indexes
    __table_args__ = (Index("idx_dismissed_scope_key", "scope", "item_key"),)

    def to_domain(self) -> DismissedItem:
        """Convert ORM model to domain model."""
        return DismissedItem(
            item_key=self.item_key,
            scope=DismissalScope(self.scope),
            reason=self.reason,
            dismissed_at=self.dismissed_at,
        )

    @classmethod
    def from_domain(cls, item: DismissedItem) -> "DismissedItemModel":
        """Create ORM model from domain model."""
        return cls(
            item_key=item.item_key,
            scope=item.scope.value,
            reason=item.reason,
            dismissed_at=item.dismissed_at,
        )


_JOBS_MIGRATION_COLUMNS = {
    "secondary_locations": "JSON",
    "is_remote": "BOOLEAN",
    "location_country": "VARCHAR(255)",
}


def _ensure_table_columns(engine: Engine) -> None:
    """Idempotently add missing nullable location columns to a legacy jobs table.

    Args:
        engine: SQLAlchemy engine instance
    """
    existing = {col["name"] for col in inspect(engine).get_columns("jobs")}
    missing = {n: t for n, t in _JOBS_MIGRATION_COLUMNS.items() if n not in existing}
    if not missing:
        return

    with engine.begin() as conn:
        for name, sql_type in missing.items():
            logger.info("Adding missing column jobs.%s", name)
            conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}"))


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

        # Add columns introduced after the table was first created
        _ensure_table_columns(engine)

        tables = inspect(engine).get_table_names()
        logger.info("Database schema ready. Tables: %s", ", ".join(tables))

    except Exception as e:
        logger.exception("Failed to create database schema: %s", e)
        raise
