"""Unit tests for the pipeline runner.

Tests the ScanPipeline orchestration including:
- Single source processing with mocked adapters
- Error handling and isolation (one source failure doesn't stop others)
- Lock behavior (prevents concurrent runs)
- Metrics collection and aggregation
- Session management and commits
- Notification integration
"""

import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.adapters.exceptions import AdapterHTTPError, AdapterTimeoutError
from app.config.environment import EnvironmentConfig
from app.config.models import (
    AdvancedConfig,
    AppConfig,
    EmailConfig,
    LoggingConfig,
    SearchCriteria,
    SourceConfig,
)
from app.domain.models import Job, RawJob
from app.matching.engine import KeywordMatcher
from app.notifications.service import NotificationService
from app.pipeline import PipelineRunResult, ScanPipeline, SourceRunStats
from app.persistence.database import close_database, init_database
from app.utils.timestamps import utc_now


@pytest.fixture
def temp_database():
    """Create a temporary in-memory database for testing."""
    init_database("sqlite:///:memory:")
    yield
    close_database()


@pytest.fixture
def app_config():
    """Basic app configuration for testing."""
    return AppConfig(
        sources=[
            SourceConfig(
                name="Test Source 1",
                type="greenhouse",
                identifier="test1",
                enabled=True,
            ),
            SourceConfig(
                name="Test Source 2",
                type="lever",
                identifier="test2",
                enabled=True,
            ),
            SourceConfig(
                name="Disabled Source",
                type="ashby",
                identifier="disabled",
                enabled=False,
            ),
        ],
        search_criteria=SearchCriteria(
            required_terms=["python"],
            keyword_groups=[["senior", "lead"]],
            exclude_terms=["contract"],
        ),
        email=EmailConfig(
            sender_email="test@example.com",
            subject_prefix="[Test]",
        ),
        scan_interval="15m",
        scan_interval_seconds=900,
        logging=LoggingConfig(level="INFO", format="key-value"),
        advanced=AdvancedConfig(),
    )


@pytest.fixture
def env_config():
    """Basic environment configuration for testing."""
    return EnvironmentConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="test@example.com",
        smtp_pass="password",
        alert_to_email="alerts@example.com",
        database_url="sqlite:///:memory:",
    )


@pytest.fixture
def mock_notification_service():
    """Mock notification service."""
    service = Mock(spec=NotificationService)
    service.send_notifications.return_value = []
    return service


@pytest.fixture
def keyword_matcher(app_config):
    """Real keyword matcher for testing."""
    return KeywordMatcher(app_config.search_criteria)


@pytest.fixture
def sample_raw_jobs():
    """Sample raw jobs for testing."""
    return [
        RawJob(
            external_id="job1",
            title="Senior Python Engineer",
            company="Tech Corp",
            location="Remote",
            description="Looking for a senior Python developer",
            url="https://example.com/jobs/1",
            posted_at=None,
        ),
        RawJob(
            external_id="job2",
            title="Lead Python Developer",
            company="Startup Inc",
            location="New York",
            description="Leading role for Python expert",
            url="https://example.com/jobs/2",
            posted_at=None,
        ),
    ]


class TestScanPipeline:
    """Test suite for ScanPipeline."""

    def test_run_once_basic_flow(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """Test basic pipeline execution with successful source processing."""
        # Create pipeline
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        # Mock adapter to return sample jobs
        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = sample_raw_jobs
            mock_get_adapter.return_value = mock_adapter

            # Run pipeline
            result = pipeline.run_once()

            # Assertions
            assert isinstance(result, PipelineRunResult)
            assert not result.skipped
            assert result.total_fetched == 4  # 2 jobs * 2 sources
            assert result.total_normalized == 4
            assert result.total_upserted == 4
            assert len(result.source_stats) == 2  # Only enabled sources
            assert not result.had_errors

    def test_run_once_skips_disabled_sources(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
    ):
        """Test that disabled sources are skipped."""
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = []
            mock_get_adapter.return_value = mock_adapter

            result = pipeline.run_once()

            # Should only process 2 enabled sources
            assert len(result.source_stats) == 2
            assert all(s.source_id != "disabled" for s in result.source_stats)

    def test_run_once_handles_adapter_errors(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """Test that adapter errors are caught and isolated."""
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_adapter = Mock()

            # First source succeeds, second fails
            def side_effect(source_config):
                if source_config.identifier == "test1":
                    mock_adapter.fetch_jobs.return_value = sample_raw_jobs
                else:
                    mock_adapter.fetch_jobs.side_effect = AdapterHTTPError(
                        "API error", status_code=500
                    )
                return mock_adapter

            mock_get_adapter.side_effect = side_effect

            result = pipeline.run_once()

            # Should process both sources despite one failing
            assert len(result.source_stats) == 2
            assert result.had_errors
            assert result.total_errors > 0

            # First source should have jobs
            stats_test1 = next(s for s in result.source_stats if s.source_id == "test1")
            assert stats_test1.fetched_count == 2
            assert not stats_test1.had_errors

            # Second source should have error
            stats_test2 = next(s for s in result.source_stats if s.source_id == "test2")
            assert stats_test2.had_errors
            assert stats_test2.error_count > 0
            assert "API error" in stats_test2.error_message

    def test_run_once_prevents_concurrent_runs(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
    ):
        """Test that concurrent runs are prevented by the lock."""
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        # Mock adapter with slow response
        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_adapter = Mock()

            def slow_fetch(*args, **kwargs):
                time.sleep(0.5)  # Simulate slow operation
                return []

            mock_adapter.fetch_jobs.side_effect = slow_fetch
            mock_get_adapter.return_value = mock_adapter

            # Start first run in background thread
            result1 = [None]

            def run_first():
                result1[0] = pipeline.run_once()

            thread = threading.Thread(target=run_first)
            thread.start()

            # Give first run time to acquire lock
            time.sleep(0.1)

            # Try to run second while first is still running
            result2 = pipeline.run_once()

            # Wait for first to complete
            thread.join()

            # Second run should be skipped
            assert result2.skipped
            assert not result1[0].skipped

    def test_run_once_handles_notification_errors(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """Test that notification errors don't crash the pipeline."""
        # Make notification service raise an error
        mock_notification_service.send_notifications.side_effect = Exception(
            "SMTP connection failed"
        )

        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = sample_raw_jobs
            mock_get_adapter.return_value = mock_adapter

            result = pipeline.run_once()

            # Pipeline should complete despite notification errors
            assert not result.skipped
            assert result.total_fetched == 4
            assert result.had_errors  # Notification error should be flagged

    def test_source_run_stats_computation(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """Test that per-source statistics are correctly computed."""
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = sample_raw_jobs
            mock_get_adapter.return_value = mock_adapter

            result = pipeline.run_once()

            # Check first source stats
            stats = result.source_stats[0]
            assert isinstance(stats, SourceRunStats)
            assert stats.source_id in ["test1", "test2"]
            assert stats.fetched_count == 2
            assert stats.normalized_count == 2
            assert stats.upserted_count == 2
            assert stats.duration_seconds > 0

    def test_pipeline_result_aggregation(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """Test that pipeline results correctly aggregate source stats."""
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = sample_raw_jobs
            mock_get_adapter.return_value = mock_adapter

            result = pipeline.run_once()

            # Aggregates should match sum of source stats
            expected_fetched = sum(s.fetched_count for s in result.source_stats)
            assert result.total_fetched == expected_fetched

            expected_normalized = sum(s.normalized_count for s in result.source_stats)
            assert result.total_normalized == expected_normalized

            # Duration should be positive
            assert result.total_duration_seconds > 0
            assert result.run_finished_at > result.run_started_at

    def test_pipeline_with_empty_sources(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
    ):
        """Test pipeline behavior when sources return no jobs."""
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = []
            mock_get_adapter.return_value = mock_adapter

            result = pipeline.run_once()

            # Should complete successfully with zero counts
            assert not result.skipped
            assert result.total_fetched == 0
            assert result.total_normalized == 0
            assert result.total_upserted == 0
            assert not result.had_errors


class TestPipelineRunResult:
    """Test suite for PipelineRunResult model."""

    def test_auto_aggregation_on_init(self):
        """Test that PipelineRunResult auto-aggregates from source stats."""
        now = utc_now()
        later = datetime.now(timezone.utc)

        source_stats = [
            SourceRunStats(
                source_id="source1",
                fetched_count=10,
                normalized_count=10,
                upserted_count=8,
                matched_count=5,
                notified_count=3,
                error_count=0,
            ),
            SourceRunStats(
                source_id="source2",
                fetched_count=5,
                normalized_count=5,
                upserted_count=5,
                matched_count=2,
                notified_count=1,
                error_count=1,
                had_errors=True,
            ),
        ]

        result = PipelineRunResult(
            run_started_at=now,
            run_finished_at=later,
            source_stats=source_stats,
        )

        assert result.total_fetched == 15
        assert result.total_normalized == 15
        assert result.total_upserted == 13
        assert result.total_matched == 7
        assert result.total_notified == 4
        assert result.total_errors == 1
        assert result.had_errors  # One source had errors

    def test_duration_computation(self):
        """Test that duration is computed from timestamps."""
        now = datetime.now(timezone.utc)
        later = datetime.fromtimestamp(now.timestamp() + 5.5, tz=timezone.utc)

        result = PipelineRunResult(
            run_started_at=now,
            run_finished_at=later,
        )

        # Duration should be approximately 5.5 seconds
        assert 5.0 <= result.total_duration_seconds <= 6.0


class TestSourceRunStats:
    """Test suite for SourceRunStats model."""

    def test_source_stats_defaults(self):
        """Test that SourceRunStats has sensible defaults."""
        stats = SourceRunStats(source_id="test")

        assert stats.source_id == "test"
        assert stats.fetched_count == 0
        assert stats.normalized_count == 0
        assert stats.upserted_count == 0
        assert stats.matched_count == 0
        assert stats.notified_count == 0
        assert stats.error_count == 0
        assert stats.duration_seconds == 0.0
        assert not stats.had_errors
        assert stats.error_message is None
