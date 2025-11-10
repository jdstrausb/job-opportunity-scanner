"""Data models for pipeline execution tracking and reporting."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class SourceRunStats:
    """
    Statistics for a single source's execution within a pipeline run.

    Attributes:
        source_id: Unique identifier for the source
        fetched_count: Number of jobs fetched from the source
        normalized_count: Number of jobs successfully normalized
        upserted_count: Number of jobs inserted or updated in database
        matched_count: Number of jobs that matched search criteria
        notified_count: Number of notifications successfully sent
        alerts_sent: Number of alerts recorded in the database
        error_count: Number of errors encountered
        duration_seconds: Time spent processing this source
        had_errors: Whether any errors occurred during processing
        error_message: Optional error message if source failed
    """

    source_id: str
    fetched_count: int = 0
    normalized_count: int = 0
    upserted_count: int = 0
    matched_count: int = 0
    notified_count: int = 0
    alerts_sent: int = 0
    error_count: int = 0
    duration_seconds: float = 0.0
    had_errors: bool = False
    error_message: Optional[str] = None


@dataclass
class PipelineRunResult:
    """
    Aggregate results from a complete pipeline execution.

    Attributes:
        run_started_at: UTC timestamp when the run began
        run_finished_at: UTC timestamp when the run completed
        total_duration_seconds: Total time for the entire run
        total_fetched: Total jobs fetched across all sources
        total_normalized: Total jobs normalized
        total_upserted: Total jobs persisted to database
        total_matched: Total jobs that matched criteria
        total_notified: Total notifications sent
        total_errors: Total errors encountered
        source_stats: Per-source execution statistics
        had_errors: Whether any source encountered errors
        alerts_sent: Number of alert records created
        skipped: Whether the run was skipped (e.g., lock already held)
    """

    run_started_at: datetime
    run_finished_at: datetime
    total_duration_seconds: float = 0.0
    total_fetched: int = 0
    total_normalized: int = 0
    total_upserted: int = 0
    total_matched: int = 0
    total_notified: int = 0
    total_errors: int = 0
    source_stats: List[SourceRunStats] = field(default_factory=list)
    had_errors: bool = False
    alerts_sent: int = 0
    skipped: bool = False

    def __post_init__(self):
        """Compute aggregate statistics from source stats if not already set."""
        if self.source_stats and self.total_fetched == 0:
            # Aggregate from source stats
            self.total_fetched = sum(s.fetched_count for s in self.source_stats)
            self.total_normalized = sum(s.normalized_count for s in self.source_stats)
            self.total_upserted = sum(s.upserted_count for s in self.source_stats)
            self.total_matched = sum(s.matched_count for s in self.source_stats)
            self.total_notified = sum(s.notified_count for s in self.source_stats)
            self.alerts_sent = sum(s.alerts_sent for s in self.source_stats)
            self.total_errors = sum(s.error_count for s in self.source_stats)
            self.had_errors = any(s.had_errors for s in self.source_stats)

        # Compute duration if not set
        if self.total_duration_seconds == 0.0:
            delta = self.run_finished_at - self.run_started_at
            self.total_duration_seconds = delta.total_seconds()
