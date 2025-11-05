"""Data models for the normalization layer.

This module defines the data structures used to track normalized jobs,
the immutable context during normalization, and the text variants needed
for matching.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.config.models import SourceConfig
from app.domain.models import Job, RawJob
from app.utils.highlighting import normalize_for_matching


@dataclass
class NormalizationContext:
    """Immutable context for a single normalization operation.

    Contains the configuration and metadata needed to normalize a RawJob
    into a Job domain model.

    Attributes:
        source_config: Configuration for this job source (type, identifier, etc.)
        scan_timestamp: When this scan/normalization is happening (UTC)
        existing_job: Optional existing Job record from persistence (for change detection)
    """

    source_config: SourceConfig
    scan_timestamp: datetime
    existing_job: Optional[Job] = None


@dataclass
class MatchableText:
    """Normalized and original text variants for keyword matching.

    Preserves original casing for presentation while providing normalized
    versions for case-insensitive keyword matching. Includes a concatenated
    full_text for quick substring checks across all fields.

    Attributes:
        title_original: Original job title (as-is from source)
        title_normalized: Lowercase, punctuation-stripped title
        description_original: Original full description
        description_normalized: Lowercase, punctuation-stripped description
        location_original: Original location string (or empty)
        location_normalized: Lowercase, punctuation-stripped location
        full_text_normalized: Concatenation of all normalized fields for quick searching
    """

    title_original: str
    title_normalized: str
    description_original: str
    description_normalized: str
    location_original: str
    location_normalized: str
    full_text_normalized: str

    @classmethod
    def from_job(cls, job: Job) -> "MatchableText":
        """Create MatchableText from a Job domain model.

        Normalizes title, description, and location using normalize_for_matching
        to prepare for keyword comparisons.

        Args:
            job: Job domain model to extract text from

        Returns:
            MatchableText instance with original and normalized variants
        """
        title_norm = normalize_for_matching(job.title)
        desc_norm = normalize_for_matching(job.description)
        loc_norm = normalize_for_matching(job.location or "")

        # Build full_text for quick substring checks
        full_text = f"{title_norm} {desc_norm} {loc_norm}".strip()

        return cls(
            title_original=job.title,
            title_normalized=title_norm,
            description_original=job.description,
            description_normalized=desc_norm,
            location_original=job.location or "",
            location_normalized=loc_norm,
            full_text_normalized=full_text,
        )


@dataclass
class NormalizationResult:
    """Result of normalizing a single RawJob.

    Captures the normalized Job, change detection information, and the
    matchable text prepared for downstream matching operations.

    Attributes:
        job: Normalized Job domain model (ready for persistence)
        existing_job: Existing job from persistence (if found), None if new
        is_new: True if this job wasn't in persistence before
        content_changed: True if hash differs from existing or if new
        matchable_text: Text variants prepared for keyword matching
        raw_job: Original raw job (preserved for debugging)
    """

    job: Job
    existing_job: Optional[Job]
    is_new: bool
    content_changed: bool
    matchable_text: MatchableText
    raw_job: RawJob

    @property
    def should_upsert(self) -> bool:
        """Whether this job should be persisted (is new or content changed)."""
        return self.is_new or self.content_changed

    @property
    def should_re_match(self) -> bool:
        """Whether this job should be re-matched against criteria.

        Re-matching is needed when content changed or it's a new job.
        """
        return self.content_changed
