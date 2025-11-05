"""Pipeline orchestration for job scanning and processing."""

import logging
import threading
import time
from typing import List, Optional
from uuid import uuid4

from app.adapters.exceptions import AdapterError
from app.adapters.factory import get_adapter
from app.config.environment import EnvironmentConfig
from app.config.models import AppConfig, SourceConfig
from app.domain.models import SourceStatus
from app.logging import get_logger
from app.logging.context import log_context
from app.matching.engine import KeywordMatcher
from app.matching.models import CandidateMatch
from app.normalization.service import JobNormalizer
from app.notifications.service import NotificationService
from app.persistence.database import get_session
from app.persistence.repositories import AlertRepository, JobRepository, SourceRepository
from app.utils.timestamps import utc_now

from .models import PipelineRunResult, SourceRunStats

logger = get_logger(__name__, component="pipeline")


class ScanPipeline:
    """
    Orchestrates a single scan across all configured sources.

    The pipeline coordinates fetching jobs from ATS systems, normalizing them,
    persisting to the database, matching against search criteria, and sending
    notifications for matches.
    """

    def __init__(
        self,
        app_config: AppConfig,
        env_config: EnvironmentConfig,
        notification_service: NotificationService,
        keyword_matcher: KeywordMatcher,
    ):
        """
        Initialize the scan pipeline.

        Args:
            app_config: Application configuration
            env_config: Environment configuration
            notification_service: Service for sending notifications
            keyword_matcher: Matcher for evaluating jobs against criteria
        """
        self.app_config = app_config
        self.env_config = env_config
        self.notification_service = notification_service
        self.keyword_matcher = keyword_matcher
        self._lock = threading.Lock()

    def run_once(self) -> PipelineRunResult:
        """
        Execute a complete scan of all enabled sources.

        This method:
        1. Acquires a lock to prevent concurrent runs
        2. Processes each enabled source sequentially
        3. For each source: fetch → normalize → persist → match → notify
        4. Aggregates metrics across all sources
        5. Returns comprehensive results

        Returns:
            PipelineRunResult with aggregate metrics and per-source stats

        Raises:
            No exceptions are raised for source-level failures; they are captured
            in the result. Only fatal configuration errors would propagate.
        """
        run_started_at = utc_now()
        run_id = uuid4().hex
        source_stats: List[SourceRunStats] = []

        # Try to acquire the lock; if already held, skip this run
        if not self._lock.acquire(blocking=False):
            with log_context(run_id=run_id):
                logger.warning(
                    "Pipeline run skipped: previous run still in progress",
                    extra={
                        "event": "pipeline.run.skipped",
                        "reason": "lock_held",
                    },
                )
            return PipelineRunResult(
                run_started_at=run_started_at,
                run_finished_at=utc_now(),
                skipped=True,
            )

        try:
            # Set run context for all logs in this pipeline execution
            with log_context(run_id=run_id):
                enabled_sources = [s for s in self.app_config.sources if s.enabled]
                disabled_sources = [s for s in self.app_config.sources if not s.enabled]

                logger.info(
                    "Pipeline run started",
                    extra={
                        "event": "pipeline.run.started",
                        "enabled_source_count": len(enabled_sources),
                        "disabled_source_count": len(disabled_sources),
                    },
                )

                # Log source enumeration
                logger.info(
                    f"Processing {len(enabled_sources)} enabled sources",
                    extra={
                        "event": "pipeline.sources.enumerated",
                        "enabled_count": len(enabled_sources),
                        "disabled_count": len(disabled_sources),
                    },
                )

                # Process each source (within run context)
                for source_config in self.app_config.sources:
                    if not source_config.enabled:
                        logger.debug(
                            f"Skipping disabled source: {source_config.name}",
                            extra={"source_id": source_config.identifier},
                        )
                        continue

                    stats = self._process_source(source_config, run_started_at, run_id)
                    source_stats.append(stats)

                # Compute final results
                run_finished_at = utc_now()
                result = PipelineRunResult(
                    run_started_at=run_started_at,
                    run_finished_at=run_finished_at,
                    source_stats=source_stats,
                )

                logger.info(
                    "Pipeline run completed",
                    extra={
                        "event": "pipeline.run.completed",
                        "duration_ms": int(result.total_duration_seconds * 1000),
                        "total_fetched": result.total_fetched,
                        "total_normalized": result.total_normalized,
                        "total_upserted": result.total_upserted,
                        "total_matched": result.total_matched,
                        "total_notified": result.total_notified,
                        "total_errors": result.total_errors,
                        "had_errors": result.had_errors,
                        "alerts_sent": result.alerts_sent,
                    },
                )

                return result

        finally:
            self._lock.release()

    def _process_source(
        self, source_config: SourceConfig, scan_timestamp, run_id: str
    ) -> SourceRunStats:
        """
        Process a single source: fetch, normalize, persist, match, notify.

        Args:
            source_config: Configuration for the source to process
            scan_timestamp: Timestamp to use for this scan run
            run_id: Run ID for context propagation

        Returns:
            SourceRunStats with metrics for this source
        """
        source_start = time.time()
        stats = SourceRunStats(source_id=source_config.identifier)

        # Set source-level context for all operations
        with log_context(
            run_id=run_id,
            source_id=source_config.identifier,
            source_name=source_config.name,
            ats_type=source_config.type,
        ):
            logger.info(
                f"Processing source: {source_config.name}",
                extra={
                    "event": "source.run.started",
                },
            )

            try:
                # Create a session for this source
                with get_session() as session:
                    # Initialize repositories
                    job_repo = JobRepository(session)
                    alert_repo = AlertRepository(session)
                    source_repo = SourceRepository(session)

                    # Initialize normalizer with shared scan timestamp
                    normalizer = JobNormalizer(job_repo, scan_timestamp=scan_timestamp)

                    # Fetch jobs from adapter
                    try:
                        adapter = get_adapter(source_config, self.app_config.advanced)
                        raw_jobs = adapter.fetch_jobs(source_config)
                        stats.fetched_count = len(raw_jobs)

                        logger.debug(
                            f"Fetched {stats.fetched_count} jobs from {source_config.name}",
                            extra={
                                "source": source_config.identifier,
                                "count": stats.fetched_count,
                            },
                        )

                        # Update source health - success
                        source_status = SourceStatus(
                            source_identifier=source_config.identifier,
                            name=source_config.name,
                            source_type=source_config.type,
                            last_success_at=scan_timestamp,
                            last_error_at=None,
                            error_message=None,
                        )
                        source_repo.upsert(source_status)

                    except AdapterError as e:
                        # Non-fatal adapter error - log and continue
                        stats.had_errors = True
                        stats.error_count += 1
                        stats.error_message = str(e)

                        logger.error(
                            f"Adapter error for {source_config.name}: {e}",
                            extra={
                                "source": source_config.identifier,
                                "error_type": type(e).__name__,
                                "error": str(e),
                            },
                            exc_info=True,
                        )

                        # Update source health - error
                        source_status = SourceStatus(
                            source_identifier=source_config.identifier,
                            name=source_config.name,
                            source_type=source_config.type,
                            last_success_at=None,
                            last_error_at=scan_timestamp,
                            error_message=str(e),
                        )
                        source_repo.upsert(source_status)

                        # Return early for this source
                        stats.duration_seconds = time.time() - source_start
                        return stats

                    # Normalize and persist jobs
                    matches_to_notify: List[CandidateMatch] = []
                    new_count = 0
                    updated_count = 0
                    unchanged_count = 0

                    for raw_job in raw_jobs:
                        try:
                            # Normalize the job
                            norm_result = normalizer.normalize(raw_job, source_config)
                            stats.normalized_count += 1

                            # Persist based on normalization result
                            if norm_result.should_upsert:
                                job_repo.upsert(norm_result.job)
                                stats.upserted_count += 1

                                if norm_result.is_new:
                                    new_count += 1
                                else:
                                    updated_count += 1
                            else:
                                # Job unchanged, just update last_seen
                                job_repo.update_last_seen(norm_result.job.job_key, scan_timestamp)
                                unchanged_count += 1

                            # Match jobs that need re-evaluation
                            if norm_result.should_re_match:
                                match_result = self.keyword_matcher.evaluate(norm_result.job)

                                if match_result.matched:
                                    stats.matched_count += 1

                                    # Create candidate match
                                    candidate = CandidateMatch(
                                        job=norm_result.job,
                                        match_result=match_result,
                                        should_notify=norm_result.is_new or norm_result.was_updated,
                                    )

                                    if candidate.should_notify:
                                        matches_to_notify.append(candidate)

                        except Exception as e:
                            # Log error but continue processing other jobs
                            stats.error_count += 1
                            logger.error(
                                f"Error processing job from {source_config.name}: {e}",
                                extra={
                                    "source": source_config.identifier,
                                    "job_id": getattr(raw_job, "id", "unknown"),
                                    "error": str(e),
                                },
                                exc_info=True,
                            )
                            continue

                    logger.info(
                        f"Normalized {stats.normalized_count} jobs: "
                        f"{new_count} new, {updated_count} updated, {unchanged_count} unchanged",
                        extra={
                            "source": source_config.identifier,
                            "new": new_count,
                            "updated": updated_count,
                            "unchanged": unchanged_count,
                        },
                    )

                    # Send notifications
                    if matches_to_notify:
                        try:
                            logger.info(
                                f"Sending {len(matches_to_notify)} notifications for {source_config.name}",
                                extra={
                                    "source": source_config.identifier,
                                    "count": len(matches_to_notify),
                                },
                            )

                            results = self.notification_service.send_notifications(
                                matches_to_notify,
                                self.env_config,
                                self.app_config.email,
                                alert_repo,
                            )

                            # Tally notification results
                            for result in results:
                                if result.sent:
                                    stats.notified_count += 1
                                    if result.should_record_alert():
                                        stats.alerts_sent += 1
                                elif result.error:
                                    stats.error_count += 1

                            # Explicitly commit to persist alert records
                            session.commit()

                            logger.info(
                                f"Notification results: {stats.notified_count} sent",
                                extra={
                                    "source": source_config.identifier,
                                    "sent": stats.notified_count,
                                    "alerts_recorded": stats.alerts_sent,
                                },
                            )

                        except Exception as e:
                            # Log notification failure but don't fail the whole source
                            stats.had_errors = True
                            stats.error_count += 1
                            logger.error(
                                f"Notification error for {source_config.name}: {e}",
                                extra={
                                    "source": source_config.identifier,
                                    "error": str(e),
                                },
                                exc_info=True,
                            )

                    # Session commits automatically on context exit if no exception

            except Exception as e:
                # Unexpected error processing this source
                stats.had_errors = True
                stats.error_count += 1
                stats.error_message = str(e)
                logger.error(
                    f"Unexpected error processing {source_config.name}: {e}",
                    extra={
                        "source": source_config.identifier,
                        "error": str(e),
                    },
                    exc_info=True,
                )

            finally:
                stats.duration_seconds = time.time() - source_start
                logger.debug(
                    f"Source processing completed: {source_config.name}",
                    extra={
                        "source": source_config.identifier,
                        "duration_seconds": stats.duration_seconds,
                        "had_errors": stats.had_errors,
                    },
                )

        return stats
