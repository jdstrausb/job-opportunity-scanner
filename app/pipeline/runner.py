"""Pipeline orchestration for job scanning and processing."""

import logging
import threading
import time
from typing import List, Optional, Tuple
from uuid import uuid4

from app.adapters.exceptions import AdapterError
from app.adapters.factory import get_adapter
from app.config.environment import EnvironmentConfig
from app.config.models import AppConfig, NotificationMode, SourceConfig
from app.domain.models import SourceStatus
from app.filtering import FilterContext, FilteringService
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
        self.filtering_service = FilteringService()
        self._lock = threading.Lock()

    def run_once(self) -> PipelineRunResult:
        """
        Execute a complete scan of all enabled sources.

        This method:
        1. Acquires a lock to prevent concurrent runs
        2. Processes each enabled source sequentially
        3. For each source: fetch → normalize → persist → match → notify
           (legacy mode) or accumulate matches (digest mode)
        4. In digest mode, sends one consolidated digest after all sources
        5. Aggregates metrics across all sources
        6. Returns comprehensive results

        Returns:
            PipelineRunResult with aggregate metrics and per-source stats

        Raises:
            No exceptions are raised for source-level failures; they are captured
            in the result. Only fatal configuration errors would propagate.
        """
        run_started_at = utc_now()
        run_id = uuid4().hex
        source_stats: List[SourceRunStats] = []
        all_digest_candidates: List[CandidateMatch] = []
        digest_matches_by_source: List[Tuple[SourceRunStats, List[CandidateMatch]]] = []

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

                    stats, digest_matches = self._process_source(
                        source_config, run_started_at, run_id
                    )
                    source_stats.append(stats)
                    if digest_matches:
                        all_digest_candidates.extend(digest_matches)
                        digest_matches_by_source.append((stats, digest_matches))

                # Compute final results
                run_finished_at = utc_now()
                result = PipelineRunResult(
                    run_started_at=run_started_at,
                    run_finished_at=run_finished_at,
                    source_stats=source_stats,
                )

                # Dispatch a single run-wide digest after all sources finish
                if all_digest_candidates:
                    self._send_global_digest(
                        all_digest_candidates, digest_matches_by_source, result, run_id
                    )

                logger.info(
                    "Pipeline run completed",
                    extra={
                        "event": "pipeline.run.completed",
                        "duration_ms": int(result.total_duration_seconds * 1000),
                        "total_fetched": result.total_fetched,
                        "total_normalized": result.total_normalized,
                        "total_filtered": result.total_filtered,
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

    def _send_global_digest(
        self,
        all_candidates: List[CandidateMatch],
        candidates_by_source: List[Tuple[SourceRunStats, List[CandidateMatch]]],
        result: PipelineRunResult,
        run_id: str,
    ) -> None:
        """
        Send one consolidated digest for the whole run and attribute counts to sources.

        Runs in its own transaction. Failures are recorded on the result
        (``had_errors`` / ``run_error_count``) and never discard persisted jobs.

        Args:
            all_candidates: Candidates accumulated across all sources
            candidates_by_source: Per-source candidates used for count attribution
            result: Run result to update (aggregates are recomputed)
            run_id: Run ID used as the digest scan identifier
        """
        logger.info(
            f"Sending global digest for {len(all_candidates)} matches "
            f"from {len(candidates_by_source)} sources",
            extra={
                "event": "pipeline.digest.sending",
                "count": len(all_candidates),
                "source_count": len(candidates_by_source),
                "notification_mode": "digest",
            },
        )

        try:
            with get_session() as session:
                alert_repo = AlertRepository(session)
                digest_result = self.notification_service.send_digest(
                    all_candidates,
                    self.env_config,
                    self.app_config.email,
                    alert_repo,
                    run_id,
                )
                # Persist alert records
                session.commit()
        except Exception as e:
            result.run_error_count += 1
            logger.error(
                f"Global digest error: {e}",
                extra={"event": "pipeline.digest.error", "error": str(e)},
                exc_info=True,
            )
            result.recompute_aggregates()
            return

        if digest_result.status == "sent":
            # The service only sends candidates whose content changed; mirror that here.
            for stats, candidates in candidates_by_source:
                sent = sum(1 for c in candidates if c.content_changed)
                stats.notified_count += sent
                stats.alerts_sent += sent
            logger.info(
                "Global digest sent",
                extra={"event": "pipeline.digest.sent", "count": len(all_candidates)},
            )
        elif digest_result.status == "failed":
            result.run_error_count += 1
            logger.error(
                f"Global digest failed: {digest_result.error}",
                extra={"event": "pipeline.digest.failed", "error": digest_result.error},
            )

        result.recompute_aggregates()

    def _process_source(
        self, source_config: SourceConfig, scan_timestamp, run_id: str
    ) -> Tuple[SourceRunStats, List[CandidateMatch]]:
        """
        Process a single source: fetch, normalize, persist, match, notify.

        Args:
            source_config: Configuration for the source to process
            scan_timestamp: Timestamp to use for this scan run
            run_id: Run ID for context propagation

        Returns:
            Tuple of SourceRunStats and the matches deferred for the global digest
            (empty unless notification mode is DIGEST).
        """
        source_start = time.time()
        stats = SourceRunStats(source_id=source_config.identifier)
        digest_matches: List[CandidateMatch] = []

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
                        return stats, digest_matches

                    # Normalize and persist jobs
                    matches_to_notify: List[CandidateMatch] = []
                    new_count = 0
                    updated_count = 0
                    unchanged_count = 0

                    # Create filter context for this source
                    filter_context = FilterContext(
                        filtering_rules=self.app_config.filtering,
                        scan_timestamp=scan_timestamp,
                        run_id=run_id,
                    )

                    for raw_job in raw_jobs:
                        try:
                            # Normalize the job
                            norm_result = normalizer.normalize(raw_job, source_config)
                            stats.normalized_count += 1

                            # Apply filtering before persistence
                            filter_result = self.filtering_service.evaluate_job(
                                norm_result.job, filter_context
                            )

                            if not filter_result.should_process:
                                # Job filtered out - log and skip
                                stats.filtered_count += 1
                                if filter_result.filter_reason:
                                    stats.filter_reasons[filter_result.filter_reason] = (
                                        stats.filter_reasons.get(filter_result.filter_reason, 0) + 1
                                    )

                                logger.debug(
                                    f"Job filtered out: {norm_result.job.title}",
                                    extra={
                                        "job_key": norm_result.job.job_key,
                                        "filter_reason": filter_result.filter_reason,
                                        "filter_metadata": filter_result.filter_metadata,
                                    },
                                )
                                continue  # Skip to next job

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
                                match_result = self.keyword_matcher.evaluate(
                                    norm_result.job, norm_result.matchable_text
                                )

                                if match_result.is_match:
                                    stats.matched_count += 1

                                    # Create candidate match
                                    candidate = CandidateMatch(
                                        normalization_result=norm_result,
                                        match_result=match_result,
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
                        f"{stats.filtered_count} filtered, "
                        f"{new_count} new, {updated_count} updated, {unchanged_count} unchanged",
                        extra={
                            "source": source_config.identifier,
                            "normalized": stats.normalized_count,
                            "filtered": stats.filtered_count,
                            "filter_reasons": stats.filter_reasons,
                            "new": new_count,
                            "updated": updated_count,
                            "unchanged": unchanged_count,
                        },
                    )

                    # Send notifications based on mode
                    if matches_to_notify:
                        if self.app_config.email.notification_mode == NotificationMode.DIGEST:
                            # Digest mode: defer to a single run-wide digest
                            digest_matches = matches_to_notify
                        else:
                            try:
                                # Legacy mode: send individual emails per job
                                logger.info(
                                    f"Sending {len(matches_to_notify)} individual notifications for {source_config.name}",
                                    extra={
                                        "source": source_config.identifier,
                                        "count": len(matches_to_notify),
                                        "notification_mode": "legacy",
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
                                    if result.is_success():
                                        stats.notified_count += 1
                                        stats.alerts_sent += 1
                                    elif result.error:
                                        stats.error_count += 1

                                logger.info(
                                    f"Notification results: {stats.notified_count} sent",
                                    extra={
                                        "source": source_config.identifier,
                                        "sent": stats.notified_count,
                                        "alerts_recorded": stats.alerts_sent,
                                    },
                                )

                                # Explicitly commit to persist alert records
                                session.commit()

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

        return stats, digest_matches
