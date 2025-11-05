"""Job normalization service for converting RawJob to Job domain model.

This module implements the normalization logic that:
1. Converts RawJob instances from adapters to Job domain models
2. Computes deterministic job_key and content_hash
3. Tracks timestamps (first_seen_at, last_seen_at)
4. Detects content changes for re-matching
5. Prepares MatchableText for downstream keyword matching
"""

import logging
import re
from datetime import datetime
from typing import Iterable, Optional

from app.config.models import SourceConfig
from app.domain.models import Job, RawJob
from app.logging import get_logger
from app.persistence.repositories import JobRepository
from app.utils.hashing import compute_content_hash, compute_job_key
from app.utils.timestamps import ensure_utc, utc_now

from .models import MatchableText, NormalizationContext, NormalizationResult

logger = get_logger(__name__, component="normalization")


class JobNormalizer:
    """Normalizes RawJob instances into canonical Job domain models.

    Responsibilities:
    - Compute deterministic job_key from source info + external_id
    - Compute content_hash for change detection
    - Handle timestamps (first_seen_at inherited, last_seen_at set to scan time)
    - Detect new vs updated vs unchanged jobs
    - Prepare MatchableText for keyword matching
    - Provide logging and error handling
    """

    def __init__(
        self,
        job_repo: JobRepository,
        scan_timestamp: Optional[datetime] = None,
        logger_instance: Optional[logging.Logger] = None,
    ):
        """Initialize JobNormalizer.

        Args:
            job_repo: JobRepository instance for looking up existing jobs
            scan_timestamp: Timestamp for this normalization scan (UTC). Defaults to utc_now()
            logger_instance: Logger instance (defaults to module logger)
        """
        self.job_repo = job_repo
        self.scan_timestamp = ensure_utc(scan_timestamp or utc_now())
        self.logger = logger_instance or logger

    def normalize(self, raw_job: RawJob, source_config: SourceConfig) -> NormalizationResult:
        """Normalize a single RawJob into a Job.

        Performs the following steps:
        1. Compute job_key from source + external_id
        2. Look up existing job (if any) from persistence
        3. Sanitize and trim text fields
        4. Derive timestamps
        5. Compute content_hash for change detection
        6. Detect new/changed status
        7. Build MatchableText for matching
        8. Return NormalizationResult

        Args:
            raw_job: Raw job from ATS adapter
            source_config: Configuration for the source

        Returns:
            NormalizationResult with normalized Job and change metadata

        Raises:
            Any exceptions from job_repo lookup are propagated
        """
        # Step 1: Compute deterministic job_key
        job_key = compute_job_key(source_config.type, source_config.identifier, raw_job.external_id)

        # Step 2: Look up existing job
        existing_job = self.job_repo.get_by_key(job_key)

        # Step 3: Sanitize text fields
        # Trim and collapse whitespace for title, description, location
        title = self._sanitize_text(raw_job.title)
        description = self._sanitize_text(raw_job.description)
        location = self._sanitize_text(raw_job.location) if raw_job.location else None

        # Log warning if description is missing/empty
        if not description:
            self.logger.warning(
                f"Missing or empty description for job {job_key}",
                extra={
                    "event": "normalization.job.missing_description",
                    "job_key": job_key,
                }
            )

        # Step 4: Derive timestamps
        # posted_at and updated_at already UTC from RawJob validators
        posted_at = raw_job.posted_at
        updated_at = raw_job.updated_at

        # seen_at is the scan_timestamp
        seen_at = self.scan_timestamp

        # first_seen_at: inherit from existing or use seen_at for new
        first_seen_at = existing_job.first_seen_at if existing_job else seen_at

        # last_seen_at always set to seen_at (current scan time)
        last_seen_at = seen_at

        # Step 5: Compute content_hash for change detection
        content_hash = compute_content_hash(title, description, location)

        # Step 6: Detect new/changed status
        is_new = existing_job is None
        content_changed = is_new or (existing_job and content_hash != existing_job.content_hash)

        # Step 7: Build MatchableText for keyword matching
        job = Job(
            job_key=job_key,
            source_type=source_config.type,
            source_identifier=source_config.identifier,
            external_id=raw_job.external_id,
            title=title,
            company=raw_job.company,
            location=location,
            description=description,
            url=raw_job.url,
            posted_at=posted_at,
            updated_at=updated_at,
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
            content_hash=content_hash,
        )

        matchable_text = MatchableText.from_job(job)

        # Step 8: Emit structured logs
        self.logger.info(
            "Normalized job",
            extra={
                "event": "normalization.job.normalized",
                "job_key": job_key,
                "company": job.company,
                "title": job.title,
                "is_new": is_new,
                "content_changed": content_changed,
            },
        )

        # Step 9: Return NormalizationResult
        return NormalizationResult(
            job=job,
            existing_job=existing_job,
            is_new=is_new,
            content_changed=content_changed,
            matchable_text=matchable_text,
            raw_job=raw_job,
        )

    def process_batch(
        self, job_configs: Iterable[tuple[RawJob, SourceConfig]]
    ) -> Iterable[NormalizationResult]:
        """Process a batch of (RawJob, SourceConfig) pairs.

        Yields results for each job while continuing on error. Reuses the same
        scan_timestamp for all jobs in the batch to ensure consistent timing.

        Args:
            job_configs: Iterable of (RawJob, SourceConfig) tuples

        Yields:
            NormalizationResult for each successfully normalized job

        Note:
            Errors during normalization are logged but don't stop batch processing.
            Check NormalizationResult for any unusual conditions.
        """
        for raw_job, source_config in job_configs:
            try:
                result = self.normalize(raw_job, source_config)
                yield result
            except Exception as e:
                self.logger.error(
                    f"Error normalizing job {raw_job.external_id} from {source_config.name}: {e}",
                    exc_info=True,
                )
                # Continue processing other jobs despite this error
                continue

    @staticmethod
    def _sanitize_text(text: Optional[str]) -> str:
        """Sanitize text field: trim, collapse whitespace.

        Args:
            text: Text to sanitize

        Returns:
            Sanitized text (empty string if input is None/empty)
        """
        if not text:
            return ""

        # Strip leading/trailing whitespace
        sanitized = text.strip()

        # Collapse multiple whitespace to single space
        sanitized = re.sub(r"\s+", " ", sanitized)

        return sanitized
