"""Scheduler service for periodic pipeline execution."""

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.logging import get_logger

logger = get_logger(__name__, component="scheduler")


class SchedulerService:
    """
    Wraps APScheduler to trigger the pipeline at configured intervals.

    Uses BackgroundScheduler to run jobs in a separate thread while
    allowing the main thread to handle signals and coordinate shutdown.
    """

    def __init__(
        self,
        pipeline_callable: Callable[[], None],
        interval_seconds: int,
        shutdown_event: Optional[threading.Event] = None,
    ):
        """
        Initialize the scheduler service.

        Args:
            pipeline_callable: Function to call on each scheduled run (e.g., pipeline.run_once)
            interval_seconds: Interval between runs in seconds
            shutdown_event: Optional event to set on shutdown for coordination
        """
        self.pipeline_callable = pipeline_callable
        self.interval_seconds = interval_seconds
        self.shutdown_event = shutdown_event

        # Configure scheduler with appropriate defaults
        self.scheduler = BackgroundScheduler(
            job_defaults={
                "max_instances": 1,  # Prevent overlapping runs
                "coalesce": True,  # If run is delayed, only execute once
                "misfire_grace_time": interval_seconds,  # Allow some delay tolerance
            },
            timezone=timezone.utc,
        )

    def start(self) -> None:
        """
        Start the scheduler and register the pipeline job.

        The first run will execute immediately after startup.
        Subsequent runs will follow the configured interval.
        """
        # Create interval trigger
        trigger = IntervalTrigger(
            seconds=self.interval_seconds,
            timezone=timezone.utc,
        )

        # Add the job with immediate first run
        next_run = datetime.now(timezone.utc)
        self.scheduler.add_job(
            func=self.pipeline_callable,
            trigger=trigger,
            id="job-scan",
            name="Job Opportunity Scan",
            replace_existing=True,
            next_run_time=next_run,  # Run immediately on startup
        )

        # Start the scheduler (spawns worker threads)
        self.scheduler.start()

        # Log scheduler started event
        logger.info(
            f"Scheduler started with interval: {self.interval_seconds} seconds",
            extra={
                "event": "scheduler.started",
                "interval_seconds": self.interval_seconds,
                "next_run_time": next_run.isoformat(),
            },
        )

    def shutdown(self, wait: bool = False) -> None:
        """
        Shutdown the scheduler gracefully.

        Args:
            wait: If True, wait for running jobs to complete before returning
        """
        logger.info(
            "Shutting down scheduler",
            extra={
                "event": "scheduler.stopping",
                "wait_for_jobs": wait,
            },
        )

        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)

        if self.shutdown_event:
            self.shutdown_event.set()

        logger.info(
            "Scheduler shutdown complete",
            extra={"event": "scheduler.stopped"}
        )

    def trigger_now(self) -> None:
        """
        Trigger an immediate run of the pipeline.

        This is useful for manual testing or administrative commands.
        The pipeline callable is executed synchronously in the current thread.
        """
        logger.info(
            "Triggering immediate pipeline run",
            extra={"event": "scheduler.trigger_now"}
        )
        self.pipeline_callable()

    def is_running(self) -> bool:
        """
        Check if the scheduler is currently running.

        Returns:
            True if scheduler is running, False otherwise
        """
        return self.scheduler.running

    def get_next_run_time(self) -> Optional[datetime]:
        """
        Get the next scheduled run time.

        Returns:
            Next run time as a datetime, or None if not scheduled
        """
        job = self.scheduler.get_job("job-scan")
        return job.next_run_time if job else None
