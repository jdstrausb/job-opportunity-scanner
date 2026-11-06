"""Data access layer (repositories) for persistence operations.

This module provides repository classes for CRUD operations on jobs, sources,
and alert records. Repositories encapsulate database operations and return
domain models rather than ORM models.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, cast

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    String,
    and_,
    case,
    cast as sql_cast,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.domain.models import (
    AlertRecord,
    DismissalScope,
    DismissedItem,
    Job,
    SourceStatus,
    TriageStatus,
)
from app.utils.timestamps import utc_now

from .exceptions import DataIntegrityError, PersistenceError, RecordNotFoundError
from .schema import AlertRecordModel, DismissedItemModel, JobModel, SourceStatusModel

logger = logging.getLogger(__name__)


_MATCH_MODES = ("matched", "all", "unmatched")
_SORT_COLUMNS = {
    "affinity_score": JobModel.affinity_score,
    "posted_at": JobModel.posted_at,
    "first_seen_at": JobModel.first_seen_at,
}
_ALERT_STATUS_FILTERS = ("alerted", "pending_alert")


@dataclass
class JobQueryFilters:
    """Filters, sorting and paging for ``JobRepository.query_jobs()``."""

    match_mode: str = "matched"  # "matched" | "all" | "unmatched"
    profile: Optional[str] = None
    source_type: Optional[str] = None
    source_identifier: Optional[str] = None
    is_remote: Optional[bool] = None
    user_status: Optional[str] = None  # "new" | "saved" | "applied"
    alert_status: Optional[str] = None  # "alerted" | "pending_alert"
    q: Optional[str] = None
    sort_by: str = "affinity_score"  # "affinity_score" | "posted_at" | "first_seen_at"
    sort_order: str = "desc"  # "desc" | "asc"
    limit: int = 50
    offset: int = 0
    include_dismissed: bool = False


@dataclass
class JobTriageRecord:
    """A stored job plus its Alert Status for the current content version."""

    job: Job
    alert_status: str  # "alerted" | "pending_alert" | "none"
    alert_sent_at: Optional[datetime] = None


@dataclass
class JobQueryPage:
    """One page of triage results with total and tab counts."""

    items: List[JobTriageRecord]
    total: int
    limit: int
    offset: int
    counts: Dict[str, int]


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so user text matches literally (escape char: ``\\``)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _dismissed_keys(scope: DismissalScope) -> Any:
    return select(DismissedItemModel.item_key).where(DismissedItemModel.scope == scope.value)


def _not_dismissed_clauses() -> List[ColumnElement[bool]]:
    """Query-time exclusion of active dismissals; never deletes ``jobs`` rows."""
    return [
        ~func.lower(func.trim(JobModel.job_key)).in_(_dismissed_keys(DismissalScope.JOB)),
        ~func.lower(func.trim(JobModel.company)).in_(_dismissed_keys(DismissalScope.COMPANY)),
        ~func.lower(func.trim(JobModel.source_identifier)).in_(
            _dismissed_keys(DismissalScope.SOURCE)
        ),
    ]


def _alert_join_condition() -> ColumnElement[bool]:
    return and_(
        AlertRecordModel.job_key == JobModel.job_key,
        AlertRecordModel.version_hash == JobModel.content_hash,
    )


def _derive_alert_status(job: Job, sent_at: Optional[datetime]) -> str:
    if sent_at is not None:
        return "alerted"
    return "pending_alert" if job.is_match else "none"


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

    def upsert(self, job: Job, *, preserve_user_status: bool = True) -> Job:
        """Insert new job or update existing job.

        Args:
            job: Job domain model to persist
            preserve_user_status: Keep an existing row's triage status instead of the
                incoming job's (freshly normalized jobs always carry ``new``)

        Returns:
            Persisted Job domain model

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            # merge() loads any existing row by primary key and copies every mapped
            # column from the new instance, so inserts and updates share one path.
            model = JobModel.from_domain(job)
            if preserve_user_status:
                existing = self.session.get(JobModel, job.job_key)
                if existing is not None and existing.user_status:
                    model.user_status = existing.user_status
            persisted = self.session.merge(model)
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

    def update_last_seen_and_match(self, job: Job, timestamp: datetime) -> None:
        """Update last_seen_at and Match Explainability for an existing job.

        Leaves ``first_seen_at``, ``user_status`` and content untouched.

        Args:
            job: Job carrying the freshly computed match metadata
            timestamp: New last_seen_at timestamp (UTC)

        Raises:
            RecordNotFoundError: If the job doesn't exist
            PersistenceError: If database error occurs
        """
        try:
            model = JobModel.from_domain(job)
            stmt = (
                update(JobModel)
                .where(JobModel.job_key == job.job_key)
                .values(
                    last_seen_at=timestamp,
                    is_match=model.is_match,
                    primary_profile=model.primary_profile,
                    matched_profiles=model.matched_profiles,
                    affinity_score=model.affinity_score,
                    score_breakdown=model.score_breakdown,
                    match_snippets=model.match_snippets,
                    matched_fields=model.matched_fields,
                )
                .execution_options(synchronize_session="fetch")
            )
            result = cast(CursorResult[Any], self.session.execute(stmt))
            self.session.flush()

            if result.rowcount == 0:
                raise RecordNotFoundError(f"Job with key {job.job_key} not found")

        except RecordNotFoundError:
            raise
        except SQLAlchemyError as e:
            logger.error(
                f"Error updating last_seen and match for job {job.job_key}: {e}", exc_info=True
            )
            raise PersistenceError(f"Failed to update last_seen and match: {e}") from e

    def update_user_status(self, job_key: str, user_status: "str | TriageStatus") -> Job:
        """Set the triage status of a job.

        Args:
            job_key: Unique job identifier
            user_status: ``new``, ``saved`` or ``applied``

        Returns:
            The updated Job

        Raises:
            ValueError: If the status is not a valid TriageStatus
            RecordNotFoundError: If job_key doesn't exist
            PersistenceError: If database error occurs
        """
        status = TriageStatus(user_status)
        try:
            model = self.session.get(JobModel, job_key)
            if model is None:
                raise RecordNotFoundError(f"Job with key {job_key} not found")
            model.user_status = status.value
            self.session.flush()
            return model.to_domain()

        except SQLAlchemyError as e:
            logger.error(f"Error updating user_status for job {job_key}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to update user_status: {e}") from e

    def get_triage_by_key(self, job_key: str) -> Optional[JobTriageRecord]:
        """Retrieve one job with its Alert Status.

        Args:
            job_key: Unique job identifier

        Returns:
            JobTriageRecord, or None if the job does not exist

        Raises:
            PersistenceError: If database error occurs
        """
        try:
            stmt = (
                select(JobModel, AlertRecordModel.sent_at)
                .outerjoin(AlertRecordModel, _alert_join_condition())
                .where(JobModel.job_key == job_key)
            )
            row = self.session.execute(stmt).first()
            if row is None:
                return None
            job = row[0].to_domain()
            return JobTriageRecord(job, _derive_alert_status(job, row[1]), row[1])

        except SQLAlchemyError as e:
            logger.error(f"Error retrieving triage record for {job_key}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to retrieve triage record: {e}") from e

    def query_jobs(self, filters: JobQueryFilters) -> JobQueryPage:
        """Query stored jobs for triage with filtering, sorting and paging.

        Active job/company/source dismissals are excluded at query time unless
        ``include_dismissed`` is set; rows are never deleted. ``counts`` cover all
        non-dismissed jobs regardless of the other filters.

        Args:
            filters: Query options

        Returns:
            JobQueryPage with the requested slice, total matches and tab counts

        Raises:
            ValueError: If an option has an unsupported value
            PersistenceError: If database error occurs
        """
        if filters.match_mode not in _MATCH_MODES:
            raise ValueError(f"match_mode must be one of {_MATCH_MODES}: {filters.match_mode}")
        if filters.sort_by not in _SORT_COLUMNS:
            raise ValueError(f"sort_by must be one of {tuple(_SORT_COLUMNS)}: {filters.sort_by}")
        if filters.sort_order not in ("asc", "desc"):
            raise ValueError(f"sort_order must be 'asc' or 'desc': {filters.sort_order}")
        if filters.alert_status is not None and filters.alert_status not in _ALERT_STATUS_FILTERS:
            raise ValueError(
                f"alert_status must be one of {_ALERT_STATUS_FILTERS}: {filters.alert_status}"
            )
        user_status = TriageStatus(filters.user_status).value if filters.user_status else None
        if filters.limit < 1 or filters.offset < 0:
            raise ValueError("limit must be >= 1 and offset >= 0")

        visible: List[ColumnElement[bool]] = (
            [] if filters.include_dismissed else _not_dismissed_clauses()
        )
        conditions = list(visible)

        if filters.match_mode == "matched":
            conditions.append(JobModel.is_match.is_(True))
        elif filters.match_mode == "unmatched":
            conditions.append(or_(JobModel.is_match.is_(False), JobModel.is_match.is_(None)))
        if filters.profile:
            pattern = f'%"{_escape_like(filters.profile)}"%'
            conditions.append(
                or_(
                    JobModel.primary_profile == filters.profile,
                    # matched_profiles is a JSON array stored as text: ["a", "b"]
                    sql_cast(JobModel.matched_profiles, String).like(pattern, escape="\\"),
                )
            )
        if filters.source_type:
            conditions.append(JobModel.source_type == filters.source_type)
        if filters.source_identifier:
            conditions.append(JobModel.source_identifier == filters.source_identifier)
        if filters.is_remote is not None:
            conditions.append(JobModel.is_remote.is_(filters.is_remote))
        if user_status:
            conditions.append(JobModel.user_status == user_status)
        if filters.q and filters.q.strip():
            pattern = f"%{_escape_like(filters.q.strip().lower())}%"
            conditions.append(
                or_(
                    *[
                        func.lower(col).like(pattern, escape="\\")
                        for col in (JobModel.title, JobModel.company, JobModel.description)
                    ]
                )
            )
        if filters.alert_status == "alerted":
            conditions.append(AlertRecordModel.sent_at.is_not(None))
        elif filters.alert_status == "pending_alert":
            conditions.append(
                and_(JobModel.is_match.is_(True), AlertRecordModel.sent_at.is_(None))
            )

        sort_col = _SORT_COLUMNS[filters.sort_by]
        primary_order = sort_col.asc() if filters.sort_order == "asc" else sort_col.desc()

        try:
            base = JobModel.__table__.join(
                AlertRecordModel.__table__, _alert_join_condition(), isouter=True
            )
            total = self.session.execute(
                select(func.count()).select_from(base).where(*conditions)
            ).scalar_one()

            stmt = (
                select(JobModel, AlertRecordModel.sent_at)
                .outerjoin(AlertRecordModel, _alert_join_condition())
                .where(*conditions)
                .order_by(primary_order, JobModel.last_seen_at.desc(), JobModel.job_key.asc())
                .limit(filters.limit)
                .offset(filters.offset)
            )
            items = []
            for model, sent_at in self.session.execute(stmt).all():
                job = model.to_domain()
                items.append(JobTriageRecord(job, _derive_alert_status(job, sent_at), sent_at))

            def count_where(expr: ColumnElement[bool]) -> Any:
                return func.coalesce(func.sum(case((expr, 1), else_=0)), 0)

            counts_row = self.session.execute(
                select(
                    count_where(JobModel.is_match.is_(True)),
                    count_where(JobModel.user_status == TriageStatus.SAVED.value),
                    count_where(JobModel.user_status == TriageStatus.APPLIED.value),
                    count_where(
                        and_(JobModel.is_match.is_(True), AlertRecordModel.sent_at.is_(None))
                    ),
                )
                .select_from(base)
                .where(*visible)
            ).one()

            return JobQueryPage(
                items=items,
                total=int(total),
                limit=filters.limit,
                offset=filters.offset,
                counts={
                    "matched": int(counts_row[0]),
                    "saved": int(counts_row[1]),
                    "applied": int(counts_row[2]),
                    "pending_alert": int(counts_row[3]),
                },
            )

        except SQLAlchemyError as e:
            logger.error(f"Error querying jobs: {e}", exc_info=True)
            raise PersistenceError(f"Failed to query jobs: {e}") from e

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


def reevaluate_stored_jobs(
    session: Session,
    keyword_matcher: Any,
    *,
    only_unscored: bool = False,
) -> int:
    """Recompute Match Explainability for stored jobs.

    Used at startup to backfill legacy rows (``is_match IS NULL``) and after
    Search Profile changes to rescore everything. Only the explainability columns
    are written; ``user_status``, ``first_seen_at`` and ``last_seen_at`` are kept.

    Args:
        session: Active SQLAlchemy session
        keyword_matcher: ``KeywordMatcher`` built from the current profiles
        only_unscored: Restrict the pass to rows where ``is_match IS NULL``

    Returns:
        Number of rows re-evaluated

    Raises:
        PersistenceError: If database error occurs
    """
    # Deferred: app.normalization imports this module, so a top-level import cycles.
    from app.normalization.models import MatchableText

    try:
        stmt = select(JobModel)
        if only_unscored:
            stmt = stmt.where(JobModel.is_match.is_(None))

        count = 0
        for model in session.execute(stmt).scalars().all():
            job = model.to_domain()
            result = keyword_matcher.evaluate(job, MatchableText.from_job(job))
            fields = JobModel.from_domain(job.apply_match_result(result))
            model.is_match = fields.is_match
            model.primary_profile = fields.primary_profile
            model.matched_profiles = fields.matched_profiles
            model.affinity_score = fields.affinity_score
            model.score_breakdown = fields.score_breakdown
            model.match_snippets = fields.match_snippets
            model.matched_fields = fields.matched_fields
            count += 1
        session.flush()
        return count

    except SQLAlchemyError as e:
        logger.error(f"Error re-evaluating stored jobs: {e}", exc_info=True)
        raise PersistenceError(f"Failed to re-evaluate stored jobs: {e}") from e


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

    def count_matching_jobs(self, scope: "str | DismissalScope", item_key: str) -> int:
        """Count stored jobs a dismissal of this scope and key would hide.

        Args:
            scope: "job", "company", or "source"
            item_key: Job key, company name, or source identifier (case-insensitive)

        Returns:
            Number of matching rows in ``jobs``

        Raises:
            ValueError: If the scope is invalid
            PersistenceError: If database error occurs
        """
        scope_value = DismissalScope(scope)
        key = item_key.strip().lower()
        column = {
            DismissalScope.JOB: JobModel.job_key,
            DismissalScope.COMPANY: JobModel.company,
            DismissalScope.SOURCE: JobModel.source_identifier,
        }[scope_value]
        try:
            stmt = select(func.count()).select_from(JobModel).where(
                func.lower(func.trim(column)) == key
            )
            return int(self.session.execute(stmt).scalar_one())
        except SQLAlchemyError as e:
            logger.error(f"Error counting jobs for {scope_value.value} {key}: {e}", exc_info=True)
            raise PersistenceError(f"Failed to count matching jobs: {e}") from e

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
