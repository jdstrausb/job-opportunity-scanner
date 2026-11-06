"""Data access layer (repositories) for persistence operations.

This module provides repository classes for CRUD operations on jobs, sources,
and alert records. Repositories encapsulate database operations and return
domain models rather than ORM models.
"""

import logging
from datetime import datetime
from typing import Any, List, Optional, cast

from sqlalchemy import CursorResult, and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.domain.models import AlertRecord, DismissalScope, DismissedItem, Job, SourceStatus
from app.utils.timestamps import utc_now

from .exceptions import DataIntegrityError, PersistenceError, RecordNotFoundError
from .schema import AlertRecordModel, DismissedItemModel, JobModel, SourceStatusModel

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
            # merge() loads any existing row by primary key and copies every mapped
            # column from the new instance, so inserts and updates share one path.
            persisted = self.session.merge(JobModel.from_domain(job))
            self.session.flush()
            return persisted.to_domain()

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
                .values(last_seen_at=timestamp)
            )
            result = cast(CursorResult[Any], self.session.execute(stmt))
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
            stmt = (
                select(JobModel)
                .where(JobModel.last_seen_at < cutoff)
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

    def get(self, source_type: str, source_identifier: str) -> Optional[SourceStatus]:
        """Retrieve the health record for one source.

        Sources are keyed by ATS type and identifier together, because the same
        identifier can be configured on more than one ATS.

        Args:
            source_type: ATS type (greenhouse, lever, ashby, rss, serpapi_google)
            source_identifier: Company identifier in the ATS

        Returns:
            SourceStatus domain model if found, None otherwise

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            source_model = self.session.get(
                SourceStatusModel,
                {"source_type": source_type, "source_identifier": source_identifier},
            )
            return source_model.to_domain() if source_model is not None else None

        except SQLAlchemyError as e:
            logger.error(
                f"Error retrieving source {source_type}/{source_identifier}: {e}", exc_info=True
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
            persisted = self.session.merge(SourceStatusModel.from_domain(source_status))
            self.session.flush()
            return persisted.to_domain()

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

    def record_fetch(
        self,
        source_type: str,
        source_identifier: str,
        *,
        name: str,
        at: datetime,
        error: Optional[str] = None,
    ) -> SourceStatus:
        """Record the outcome of one fetch attempt for a source.

        A success sets ``last_success_at`` and clears any previous error. A failure
        sets ``last_error_at`` and ``error_message`` and leaves ``last_success_at``
        untouched, so the last known-good scan time survives outages. ``name`` is
        refreshed on every call. Creates the row if missing.

        Args:
            source_type: ATS type (greenhouse, lever, ashby, rss, serpapi_google)
            source_identifier: Company identifier in the ATS
            name: Human-readable source name
            at: When the fetch completed (UTC)
            error: Error description if the fetch failed, None on success

        Returns:
            The source's health record after this outcome

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            model = self.session.get(
                SourceStatusModel,
                {"source_type": source_type, "source_identifier": source_identifier},
            )
            if model is None:
                model = SourceStatusModel(
                    source_type=source_type, source_identifier=source_identifier
                )
                self.session.add(model)

            model.name = name
            if error is None:
                model.last_success_at = at
                model.last_error_at = None
                model.error_message = None
            else:
                model.last_error_at = at
                model.error_message = error

            self.session.flush()
            return model.to_domain()

        except SQLAlchemyError as e:
            logger.error(
                f"Error recording fetch for source {source_type}/{source_identifier}: {e}", exc_info=True
            )
            raise PersistenceError(f"Failed to record source fetch: {e}") from e


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
            stmt = delete(AlertRecordModel).where(AlertRecordModel.sent_at < cutoff)
            result = cast(CursorResult[Any], self.session.execute(stmt))
            self.session.flush()

            deleted_count = result.rowcount
            logger.info(f"Cleaned up {deleted_count} old alert records")
            return deleted_count

        except SQLAlchemyError as e:
            logger.error(f"Error cleaning up old alerts: {e}", exc_info=True)
            raise PersistenceError(f"Failed to cleanup old alerts: {e}") from e


class DismissedItemRepository:
    """Repository for persistent job/company/source dismissals."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session for database operations
        """
        self.session = session

    def dismiss(
        self,
        item_key: str,
        scope: "str | DismissalScope" = "job",
        reason: Optional[str] = None,
        dismissed_at: Optional[datetime] = None,
    ) -> DismissedItem:
        """Dismiss an item (idempotent upsert of reason and dismissed_at).

        Args:
            item_key: Job key, company name, or source identifier (normalized)
            scope: "job", "company", or "source"
            reason: Optional reason
            dismissed_at: Dismissal time (defaults to now, UTC)

        Returns:
            Persisted DismissedItem

        Raises:
            ValueError: If the scope is invalid
            PersistenceError: If database error occurs
        """
        item = DismissedItem(
            item_key=item_key,
            scope=DismissalScope(scope),
            reason=reason,
            dismissed_at=dismissed_at or utc_now(),
        )
        try:
            existing = self.session.get(
                DismissedItemModel,
                {"item_key": item.item_key, "scope": item.scope.value},
            )
            if existing is not None:
                existing.reason = item.reason
                existing.dismissed_at = item.dismissed_at
                self.session.flush()
                return existing.to_domain()

            model = DismissedItemModel.from_domain(item)
            self.session.add(model)
            self.session.flush()
            return model.to_domain()
        except SQLAlchemyError as e:
            logger.error(f"Error dismissing {item.scope.value} {item.item_key}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to dismiss item: {e}") from e

    def undismiss(self, item_key: str, scope: "str | DismissalScope" = "job") -> bool:
        """Remove a dismissal.

        Args:
            item_key: Key to un-dismiss (normalized)
            scope: "job", "company", or "source"

        Returns:
            True if a record was deleted, False if absent

        Raises:
            PersistenceError: If database error occurs
        """
        key = item_key.strip().lower()
        scope_value = DismissalScope(scope).value
        try:
            result = cast(
                CursorResult[Any],
                self.session.execute(
                    delete(DismissedItemModel).where(
                        DismissedItemModel.item_key == key,
                        DismissedItemModel.scope == scope_value,
                    )
                ),
            )
            self.session.flush()
            return bool(result.rowcount)
        except SQLAlchemyError as e:
            logger.error(f"Error undismissing {scope_value} {key}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to undismiss item: {e}") from e

    def is_dismissed(self, job: Job) -> "tuple[bool, Optional[DismissedItem]]":
        """Check whether a job, its company, or its source is dismissed.

        Uses a single query with OR across the three scopes. If several match,
        the most specific one wins (job, then company, then source).

        Args:
            job: Job to check

        Returns:
            Tuple of (is_dismissed, matching DismissedItem or None)

        Raises:
            PersistenceError: If database error occurs
        """
        keys = {
            DismissalScope.JOB.value: job.job_key.strip().lower(),
            DismissalScope.COMPANY.value: (job.company or "").strip().lower(),
            DismissalScope.SOURCE.value: (job.source_identifier or "").strip().lower(),
        }
        try:
            stmt = select(DismissedItemModel).where(
                or_(
                    *[
                        and_(DismissedItemModel.scope == scope, DismissedItemModel.item_key == key)
                        for scope, key in keys.items()
                        if key
                    ]
                )
            )
            matches: "dict[str, DismissedItemModel]" = {
                str(m.scope): m for m in self.session.execute(stmt).scalars().all()
            }
            for scope in (
                DismissalScope.JOB.value,
                DismissalScope.COMPANY.value,
                DismissalScope.SOURCE.value,
            ):
                if scope in matches:
                    return True, matches[scope].to_domain()
            return False, None
        except SQLAlchemyError as e:
            logger.error(f"Error checking dismissal for job {job.job_key}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to check dismissal: {e}") from e

    def list_all(self, scope: "Optional[str | DismissalScope]" = None) -> List[DismissedItem]:
        """List dismissals, optionally filtered by scope.

        Args:
            scope: Optional scope filter

        Returns:
            Dismissed items ordered by scope then key

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            stmt = select(DismissedItemModel).order_by(
                DismissedItemModel.scope, DismissedItemModel.item_key
            )
            if scope is not None:
                stmt = stmt.where(DismissedItemModel.scope == DismissalScope(scope).value)
            return [m.to_domain() for m in self.session.execute(stmt).scalars().all()]
        except SQLAlchemyError as e:
            logger.error(f"Error listing dismissed items: {e}", exc_info=True)
            raise PersistenceError(f"Failed to list dismissed items: {e}") from e
