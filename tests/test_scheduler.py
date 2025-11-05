"""Unit tests for the scheduler service.

Tests the SchedulerService including:
- Job registration with correct configuration
- Immediate first run (next_run_time set to now)
- Prevents overlapping runs (max_instances=1)
- Coalescing behavior
- Start/shutdown lifecycle
- Trigger now functionality
"""

import threading
import time
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.scheduler import SchedulerService


class TestSchedulerService:
    """Test suite for SchedulerService."""

    def test_scheduler_initialization(self):
        """Test that scheduler initializes with correct parameters."""
        mock_callable = Mock()
        shutdown_event = threading.Event()

        scheduler = SchedulerService(
            pipeline_callable=mock_callable,
            interval_seconds=60,
            shutdown_event=shutdown_event,
        )

        assert scheduler.interval_seconds == 60
        assert scheduler.pipeline_callable == mock_callable
        assert scheduler.shutdown_event == shutdown_event
        assert not scheduler.is_running()

    def test_scheduler_start_and_shutdown(self):
        """Test scheduler start and shutdown lifecycle."""
        mock_callable = Mock()
        shutdown_event = threading.Event()

        scheduler = SchedulerService(
            pipeline_callable=mock_callable,
            interval_seconds=300,
            shutdown_event=shutdown_event,
        )

        # Start scheduler
        scheduler.start()
        assert scheduler.is_running()

        # Wait a moment for scheduler to initialize
        time.sleep(0.1)

        # Shutdown
        scheduler.shutdown(wait=False)
        assert not scheduler.is_running()
        assert shutdown_event.is_set()

    def test_scheduler_registers_job_with_correct_config(self):
        """Test that job is registered with max_instances=1 and coalesce=True."""
        mock_callable = Mock()

        scheduler = SchedulerService(
            pipeline_callable=mock_callable,
            interval_seconds=60,
        )

        # Check job defaults
        assert scheduler.scheduler.job_defaults["max_instances"] == 1
        assert scheduler.scheduler.job_defaults["coalesce"] is True
        assert scheduler.scheduler.job_defaults["misfire_grace_time"] == 60

    def test_scheduler_immediate_first_run(self):
        """Test that first run is scheduled immediately."""
        call_count = [0]

        def counting_callable():
            call_count[0] += 1

        scheduler = SchedulerService(
            pipeline_callable=counting_callable,
            interval_seconds=10,
        )

        scheduler.start()

        # Wait for immediate first run
        time.sleep(1.5)

        scheduler.shutdown(wait=True)

        # Should have been called at least once
        assert call_count[0] >= 1

    def test_scheduler_prevents_concurrent_runs(self):
        """Test that max_instances=1 prevents concurrent executions."""
        execution_times = []

        def slow_callable():
            start = time.time()
            execution_times.append(start)
            time.sleep(0.5)

        scheduler = SchedulerService(
            pipeline_callable=slow_callable,
            interval_seconds=1,  # Very short interval
        )

        scheduler.start()
        time.sleep(2.5)  # Let multiple intervals pass
        scheduler.shutdown(wait=True)

        # Due to max_instances=1, executions should not overlap
        # Even with 1-second interval and 0.5-second execution time,
        # we shouldn't get more than ~3 executions in 2.5 seconds
        assert len(execution_times) <= 3

    def test_trigger_now_executes_immediately(self):
        """Test that trigger_now executes the callable synchronously."""
        call_count = [0]

        def counting_callable():
            call_count[0] += 1

        scheduler = SchedulerService(
            pipeline_callable=counting_callable,
            interval_seconds=3600,  # Long interval, won't trigger naturally
        )

        # Don't start scheduler, just trigger manually
        scheduler.trigger_now()

        assert call_count[0] == 1

    def test_get_next_run_time(self):
        """Test getting the next scheduled run time."""
        mock_callable = Mock()

        scheduler = SchedulerService(
            pipeline_callable=mock_callable,
            interval_seconds=60,
        )

        # Before starting, no job is registered
        assert scheduler.get_next_run_time() is None

        # Start scheduler
        scheduler.start()
        time.sleep(0.1)

        # Should have a next run time
        next_run = scheduler.get_next_run_time()
        assert next_run is not None
        assert isinstance(next_run, datetime)

        scheduler.shutdown(wait=False)

    def test_scheduler_interval_accuracy(self):
        """Test that scheduler respects the configured interval."""
        execution_times = []

        def recording_callable():
            execution_times.append(time.time())

        scheduler = SchedulerService(
            pipeline_callable=recording_callable,
            interval_seconds=1,
        )

        scheduler.start()
        time.sleep(3.5)  # Allow 3-4 executions
        scheduler.shutdown(wait=True)

        # Should have at least 3 executions
        assert len(execution_times) >= 3

        # Check intervals between executions
        if len(execution_times) >= 2:
            intervals = [
                execution_times[i + 1] - execution_times[i]
                for i in range(len(execution_times) - 1)
            ]
            # Intervals should be approximately 1 second (with some tolerance)
            for interval in intervals:
                assert 0.8 <= interval <= 1.5

    def test_scheduler_with_no_shutdown_event(self):
        """Test that scheduler works without a shutdown event."""
        mock_callable = Mock()

        scheduler = SchedulerService(
            pipeline_callable=mock_callable,
            interval_seconds=60,
            shutdown_event=None,  # No shutdown event
        )

        scheduler.start()
        assert scheduler.is_running()

        scheduler.shutdown(wait=False)
        assert not scheduler.is_running()

    def test_scheduler_shutdown_with_wait(self):
        """Test shutdown with wait=True waits for running jobs."""
        execution_started = threading.Event()
        execution_completed = threading.Event()

        def slow_callable():
            execution_started.set()
            time.sleep(0.5)
            execution_completed.set()

        scheduler = SchedulerService(
            pipeline_callable=slow_callable,
            interval_seconds=10,
        )

        scheduler.start()

        # Wait for execution to start
        execution_started.wait(timeout=2)

        # Shutdown with wait=True
        scheduler.shutdown(wait=True)

        # Execution should have completed
        assert execution_completed.is_set()

    def test_multiple_start_calls_safe(self):
        """Test that calling start multiple times doesn't cause issues."""
        mock_callable = Mock()

        scheduler = SchedulerService(
            pipeline_callable=mock_callable,
            interval_seconds=60,
        )

        scheduler.start()
        assert scheduler.is_running()

        # Second start should be safe (APScheduler handles this)
        # This shouldn't raise an exception
        try:
            scheduler.start()
        except Exception:
            pytest.fail("Multiple start() calls should be handled gracefully")

        scheduler.shutdown(wait=False)

    def test_scheduler_callable_exceptions_dont_stop_scheduler(self):
        """Test that exceptions in callable don't stop the scheduler."""
        call_count = [0]

        def failing_callable():
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Intentional error")

        scheduler = SchedulerService(
            pipeline_callable=failing_callable,
            interval_seconds=1,
        )

        scheduler.start()
        time.sleep(2.5)
        scheduler.shutdown(wait=True)

        # Should have been called multiple times despite first failure
        assert call_count[0] >= 2
