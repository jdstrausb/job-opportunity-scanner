"""Data access layer (repositories) for persistence operations.

This module provides repository classes for CRUD operations on jobs, sources,
and alert records. Repositories encapsulate database operations and return
domain models rather than ORM models.
"""

import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.domain.models import AlertRecord, Job, SourceStatus

from .exceptions import DataIntegrityError, PersistenceError, RecordNotFoundError
from .schema import AlertRecordModel, JobModel, SourceStatusModel

logger = logging.getLogger(__name__)


class JobRepository:
    """Repository for job-related database operations."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session for database operations
        """
        self.session = session

    def get_by_key(self, job_key: str) -> Optional[Job]:
        """Retrieve job by primary key.

        Args:
            job_key: Unique job identifier

        Returns:
            Job domain model if found, None otherwise

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            stmt = select(JobModel).where(JobModel.job_key == job_key)
            result = self.session.execute(stmt)
            job_model = result.scalar_one_or_none()

            if job_model is None:
                return None

            return job_model.to_domain()

        except SQLAlchemyError as e:
            logger.error(f"Error retrieving job by key {job_key}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to retrieve job: {e}") from e

    def get_by_source(self, source_type: str, source_identifier: str) -> List[Job]:
        """Query all jobs for a given source.

        Args:
            source_type: ATS type (greenhouse, lever, ashby)
            source_identifier: Company identifier in the ATS

        Returns:
            List of Job domain models (empty list if none found)

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            stmt = (
                select(JobModel)
                .where(
                    JobModel.source_type == source_type,
                    JobModel.source_identifier == source_identifier,
                )
                .order_by(JobModel.last_seen_at.desc())
            )
            result = self.session.execute(stmt)
            job_models = result.scalars().all()

            return [job_model.to_domain() for job_model in job_models]

        except SQLAlchemyError as e:
            logger.error(
                f"Error retrieving jobs for source {source_type}/{source_identifier}: {e}",
                exc_info=True,
            )
            raise PersistenceError(f"Failed to retrieve jobs: {e}") from e

    def upsert(self, job: Job) -> Job:
        """Insert new job or update existing job.

        Args:
            job: Job domain model to persist

        Returns:
            Persisted Job domain model

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            # Check if job exists
            existing = self.session.get(JobModel, job.job_key)

            if existing:
                # Update existing job
                existing.source_type = job.source_type
                existing.source_identifier = job.source_identifier
                existing.external_id = job.external_id
                existing.title = job.title
                existing.company = job.company
                existing.location = job.location
                existing.description = job.description
                existing.url = job.url
                existing.posted_at = (
                    job.posted_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if job.posted_at else None
                )
                existing.updated_at = (
                    job.updated_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    if job.updated_at
                    else None
                )
                existing.first_seen_at = job.first_seen_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                existing.last_seen_at = job.last_seen_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                existing.content_hash = job.content_hash

                self.session.flush()
                return existing.to_domain()
            else:
                # Insert new job
                job_model = JobModel.from_domain(job)
                self.session.add(job_model)
                self.session.flush()
                return job_model.to_domain()

        except IntegrityError as e:
            logger.error(f"Integrity error upserting job {job.job_key}: {e}", exc_info=True)
            raise DataIntegrityError(f"Failed to upsert job due to constraint violation: {e}") from e
        except SQLAlchemyError as e:
            logger.error(f"Error upserting job {job.job_key}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to upsert job: {e}") from e

    def update_last_seen(self, job_key: str, timestamp: datetime) -> None:
        """Update only the last_seen_at timestamp.

        Args:
            job_key: Unique job identifier
            timestamp: New last_seen_at timestamp (UTC)

        Raises:
            RecordNotFoundError: If job_key doesn't exist
            PersistenceError: If database error occurs
        """
        try:
            stmt = (
                update(JobModel)
                .where(JobModel.job_key == job_key)
                .values(last_seen_at=timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
            )
            result = self.session.execute(stmt)
            self.session.flush()

            if result.rowcount == 0:
                raise RecordNotFoundError(f"Job with key {job_key} not found")

        except RecordNotFoundError:
            raise
        except SQLAlchemyError as e:
            logger.error(f"Error updating last_seen for job {job_key}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to update last_seen: {e}") from e

    def bulk_upsert(self, jobs: List[Job]) -> List[Job]:
        """Efficiently upsert multiple jobs in single transaction.

        Args:
            jobs: List of Job domain models to persist

        Returns:
            List of persisted Job domain models

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            persisted_jobs = []

            for job in jobs:
                persisted_job = self.upsert(job)
                persisted_jobs.append(persisted_job)

            return persisted_jobs

        except PersistenceError:
            raise
        except Exception as e:
            logger.error(f"Error in bulk upsert: {e}", exc_info=True)
            raise PersistenceError(f"Failed to bulk upsert jobs: {e}") from e

    def get_stale_jobs(self, cutoff: datetime) -> List[Job]:
        """Find jobs not seen since cutoff timestamp.

        Args:
            cutoff: Datetime cutoff (jobs with last_seen_at before this are stale)

        Returns:
            List of Job domain models (ordered by last_seen_at ASC)

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            stmt = (
                select(JobModel)
                .where(JobModel.last_seen_at < cutoff_str)
                .order_by(JobModel.last_seen_at.asc())
            )
            result = self.session.execute(stmt)
            job_models = result.scalars().all()

            return [job_model.to_domain() for job_model in job_models]

        except SQLAlchemyError as e:
            logger.error(f"Error retrieving stale jobs: {e}", exc_info=True)
            raise PersistenceError(f"Failed to retrieve stale jobs: {e}") from e


class SourceRepository:
    """Repository for source status and health tracking operations."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session for database operations
        """
        self.session = session

    def get_by_identifier(self, source_identifier: str) -> Optional[SourceStatus]:
        """Retrieve source status by identifier.

        Args:
            source_identifier: Company identifier in the ATS

        Returns:
            SourceStatus domain model if found, None otherwise

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            stmt = select(SourceStatusModel).where(
                SourceStatusModel.source_identifier == source_identifier
            )
            result = self.session.execute(stmt)
            source_model = result.scalar_one_or_none()

            if source_model is None:
                return None

            return source_model.to_domain()

        except SQLAlchemyError as e:
            logger.error(
                f"Error retrieving source by identifier {source_identifier}: {e}", exc_info=True
            )
            raise PersistenceError(f"Failed to retrieve source: {e}") from e

    def get_all(self) -> List[SourceStatus]:
        """Retrieve all source status records.

        Returns:
            List of SourceStatus domain models (ordered by name)

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            stmt = select(SourceStatusModel).order_by(SourceStatusModel.name)
            result = self.session.execute(stmt)
            source_models = result.scalars().all()

            return [source_model.to_domain() for source_model in source_models]

        except SQLAlchemyError as e:
            logger.error(f"Error retrieving all sources: {e}", exc_info=True)
            raise PersistenceError(f"Failed to retrieve sources: {e}") from e

    def upsert(self, source_status: SourceStatus) -> SourceStatus:
        """Insert new source or update existing.

        Args:
            source_status: SourceStatus domain model to persist

        Returns:
            Persisted SourceStatus domain model

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            # Check if source exists
            existing = self.session.get(SourceStatusModel, source_status.source_identifier)

            if existing:
                # Update existing source
                existing.name = source_status.name
                existing.source_type = source_status.source_type
                existing.last_success_at = (
                    source_status.last_success_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    if source_status.last_success_at
                    else None
                )
                existing.last_error_at = (
                    source_status.last_error_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    if source_status.last_error_at
                    else None
                )
                existing.error_message = source_status.error_message

                self.session.flush()
                return existing.to_domain()
            else:
                # Insert new source
                source_model = SourceStatusModel.from_domain(source_status)
                self.session.add(source_model)
                self.session.flush()
                return source_model.to_domain()

        except IntegrityError as e:
            logger.error(
                f"Integrity error upserting source {source_status.source_identifier}: {e}",
                exc_info=True,
            )
            raise DataIntegrityError(
                f"Failed to upsert source due to constraint violation: {e}"
            ) from e
        except SQLAlchemyError as e:
            logger.error(
                f"Error upserting source {source_status.source_identifier}: {e}", exc_info=True
            )
            raise PersistenceError(f"Failed to upsert source: {e}") from e

    def update_success(self, source_identifier: str, timestamp: datetime) -> None:
        """Update last_success_at after successful scan.

        Clears last_error_at and error_message. Creates source if doesn't exist.

        Args:
            source_identifier: Company identifier in the ATS
            timestamp: Success timestamp (UTC)

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            existing = self.session.get(SourceStatusModel, source_identifier)

            if existing:
                # Update existing source
                existing.last_success_at = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                existing.last_error_at = None
                existing.error_message = None
                self.session.flush()
            else:
                # Create new source with minimal data
                source_status = SourceStatus(
                    source_identifier=source_identifier,
                    name=source_identifier,  # Use identifier as name if not known
                    source_type="unknown",  # Placeholder
                    last_success_at=timestamp,
                    last_error_at=None,
                    error_message=None,
                )
                self.upsert(source_status)

        except SQLAlchemyError as e:
            logger.error(
                f"Error updating success for source {source_identifier}: {e}", exc_info=True
            )
            raise PersistenceError(f"Failed to update source success: {e}") from e

    def update_error(
        self, source_identifier: str, timestamp: datetime, error_message: str
    ) -> None:
        """Update last_error_at and error_message after failure.

        Keeps last_success_at unchanged. Creates source if doesn't exist.

        Args:
            source_identifier: Company identifier in the ATS
            timestamp: Error timestamp (UTC)
            error_message: Error description

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            existing = self.session.get(SourceStatusModel, source_identifier)

            if existing:
                # Update existing source
                existing.last_error_at = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                existing.error_message = error_message
                self.session.flush()
            else:
                # Create new source with minimal data
                source_status = SourceStatus(
                    source_identifier=source_identifier,
                    name=source_identifier,  # Use identifier as name if not known
                    source_type="unknown",  # Placeholder
                    last_success_at=None,
                    last_error_at=timestamp,
                    error_message=error_message,
                )
                self.upsert(source_status)

        except SQLAlchemyError as e:
            logger.error(
                f"Error updating error for source {source_identifier}: {e}", exc_info=True
            )
            raise PersistenceError(f"Failed to update source error: {e}") from e


class AlertRepository:
    """Repository for alert tracking operations."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session for database operations
        """
        self.session = session

    def has_been_sent(self, job_key: str, version_hash: str) -> bool:
        """Check if alert already sent for this job version.

        Args:
            job_key: Unique job identifier
            version_hash: Content hash for this job version

        Returns:
            True if alert already sent, False otherwise

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            stmt = select(AlertRecordModel).where(
                AlertRecordModel.job_key == job_key,
                AlertRecordModel.version_hash == version_hash,
            )
            result = self.session.execute(stmt)
            alert_model = result.scalar_one_or_none()

            return alert_model is not None

        except SQLAlchemyError as e:
            logger.error(
                f"Error checking alert status for job {job_key}, version {version_hash}: {e}",
                exc_info=True,
            )
            raise PersistenceError(f"Failed to check alert status: {e}") from e

    def record_alert(self, job_key: str, version_hash: str, sent_at: datetime) -> AlertRecord:
        """Insert alert record after successful notification.

        Uses INSERT OR IGNORE to handle duplicates gracefully (idempotent).

        Args:
            job_key: Unique job identifier
            version_hash: Content hash for this job version
            sent_at: When alert was sent (UTC)

        Returns:
            Persisted AlertRecord domain model

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            # Check if alert already exists
            existing = self.session.get(AlertRecordModel, {"job_key": job_key, "version_hash": version_hash})

            if existing:
                # Already exists, return existing record (idempotent)
                logger.debug(f"Alert already recorded for job {job_key}, version {version_hash}")
                return existing.to_domain()
            else:
                # Insert new alert record
                alert_record = AlertRecord(
                    job_key=job_key, version_hash=version_hash, sent_at=sent_at
                )
                alert_model = AlertRecordModel.from_domain(alert_record)
                self.session.add(alert_model)
                self.session.flush()
                return alert_model.to_domain()

        except IntegrityError as e:
            # Duplicate key - return existing record (idempotent behavior)
            logger.debug(
                f"Duplicate alert record for job {job_key}, version {version_hash} (expected in race conditions)"
            )
            existing = self.session.get(AlertRecordModel, {"job_key": job_key, "version_hash": version_hash})
            if existing:
                return existing.to_domain()
            raise DataIntegrityError(f"Failed to record alert: {e}") from e
        except SQLAlchemyError as e:
            logger.error(
                f"Error recording alert for job {job_key}, version {version_hash}: {e}",
                exc_info=True,
            )
            raise PersistenceError(f"Failed to record alert: {e}") from e

    def get_alerts_for_job(self, job_key: str) -> List[AlertRecord]:
        """Retrieve all alerts sent for a job (across all versions).

        Args:
            job_key: Unique job identifier

        Returns:
            List of AlertRecord domain models (ordered by sent_at DESC)

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            stmt = (
                select(AlertRecordModel)
                .where(AlertRecordModel.job_key == job_key)
                .order_by(AlertRecordModel.sent_at.desc())
            )
            result = self.session.execute(stmt)
            alert_models = result.scalars().all()

            return [alert_model.to_domain() for alert_model in alert_models]

        except SQLAlchemyError as e:
            logger.error(f"Error retrieving alerts for job {job_key}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to retrieve alerts: {e}") from e

    def cleanup_old_alerts(self, cutoff: datetime) -> int:
        """Delete alert records older than cutoff.

        Args:
            cutoff: Datetime cutoff (alerts before this are deleted)

        Returns:
            Count of deleted records

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            stmt = delete(AlertRecordModel).where(AlertRecordModel.sent_at < cutoff_str)
            result = self.session.execute(stmt)
            self.session.flush()

            deleted_count = result.rowcount
            logger.info(f"Cleaned up {deleted_count} old alert records")
            return deleted_count

        except SQLAlchemyError as e:
            logger.error(f"Error cleaning up old alerts: {e}", exc_info=True)
            raise PersistenceError(f"Failed to cleanup old alerts: {e}") from e
