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
    NotificationMode,
    SearchCriteria,
    SourceConfig,
)
from app.domain.models import Job, RawJob
from app.matching.engine import KeywordMatcher
from app.notifications.models import NotificationResult
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
            title="Software Engineer",
            company="Tech Corp",
            location="Remote",
            description="Looking for a senior Python developer working remotely",
            url="https://example.com/jobs/1",
            posted_at=None,
        ),
        RawJob(
            external_id="job2",
            title="Software Engineer",
            company="Startup Inc",
            location="Remote",
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

    def test_run_once_skips_dismissed_jobs_and_companies(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """Dismissed job keys and companies are dropped before upsert/match/alert."""
        from app.persistence.database import get_session
        from app.persistence.repositories import DismissedItemRepository, JobRepository
        from app.utils.hashing import compute_job_key

        dismissed_key = compute_job_key("greenhouse", "test1", "job1")
        with get_session() as session:
            repo = DismissedItemRepository(session)
            repo.dismiss(dismissed_key, scope="job")
            repo.dismiss("Startup Inc", scope="company")

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

        by_source = {s.source_id: s for s in result.source_stats}
        # test1: job1 dismissed by key, job2 dismissed by company
        assert by_source["test1"].filtered_count == 2
        assert by_source["test1"].upserted_count == 0
        assert by_source["test1"].filter_reasons == {
            "dismissed_job": 1,
            "dismissed_company": 1,
        }
        # test2: only job2 (company Startup Inc) dismissed
        assert by_source["test2"].filtered_count == 1
        assert by_source["test2"].upserted_count == 1
        assert by_source["test2"].filter_reasons == {"dismissed_company": 1}

        with get_session() as session:
            job_repo = JobRepository(session)
            assert job_repo.get_by_key(dismissed_key) is None
            assert len(job_repo.get_by_source("greenhouse", "test1")) == 0

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
            def side_effect(source_config, advanced_config, env_config=None):
                if source_config.identifier == "test1":
                    mock_adapter.fetch_jobs.return_value = sample_raw_jobs
                else:
                    mock_adapter.fetch_jobs.side_effect = AdapterHTTPError(
                        "API error", url="http://test.com", status_code=500
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

    def test_source_health_survives_failed_fetch_and_clears_on_recovery(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
    ):
        """An adapter error records the failure without erasing last_success_at."""
        from app.persistence.database import get_session
        from app.persistence.repositories import SourceRepository

        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        def health(identifier):
            with get_session() as session:
                return SourceRepository(session).get("greenhouse", identifier)

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            ok_adapter = Mock()
            ok_adapter.fetch_jobs.return_value = []
            failing_adapter = Mock()
            failing_adapter.fetch_jobs.side_effect = AdapterHTTPError(
                "API error", url="http://test.com", status_code=500
            )

            mock_get_adapter.return_value = ok_adapter
            pipeline.run_once()
            first_success = health("test1").last_success_at
            assert first_success is not None

            mock_get_adapter.return_value = failing_adapter
            pipeline.run_once()
            after_failure = health("test1")
            assert after_failure.last_success_at == first_success
            assert after_failure.last_error_at is not None
            assert "API error" in after_failure.error_message
            assert after_failure.name == "Test Source 1"
            assert after_failure.source_type == "greenhouse"

            mock_get_adapter.return_value = ok_adapter
            pipeline.run_once()
            recovered = health("test1")
            assert recovered.last_success_at > first_success
            assert recovered.last_error_at is None
            assert recovered.error_message is None

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

        app_config.email.notification_mode = NotificationMode.LEGACY

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

    def test_notification_counts_are_tallied(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """Test that successful notifications are correctly counted."""
        # Mock notification service to return successful results
        notification_results = [
            NotificationResult(
                job_key="job1", version_hash="v1", attempts=1, status="sent"
            ),
            NotificationResult(
                job_key="job2", version_hash="v2", attempts=1, status="sent"
            ),
        ]
        mock_notification_service.send_notifications.return_value = notification_results

        app_config.email.notification_mode = NotificationMode.LEGACY

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

            # Assert that notification counts are aggregated correctly
            assert result.total_notified == 4
            assert result.alerts_sent == 4
            assert not result.had_errors

    @staticmethod
    def _per_source_adapter(jobs_by_source):
        """Build a get_adapter side_effect returning distinct jobs per source."""

        def side_effect(source_config, advanced_config, env_config=None):
            adapter = Mock()
            adapter.fetch_jobs.return_value = jobs_by_source[source_config.identifier]
            return adapter

        return side_effect

    def test_run_once_sends_single_global_digest_across_sources(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """Digest mode sends one combined digest for all sources."""
        app_config.email.notification_mode = NotificationMode.DIGEST
        mock_notification_service.send_digest.return_value = NotificationResult(
            job_key="digest", version_hash="v", attempts=1, status="sent"
        )
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_get_adapter.side_effect = self._per_source_adapter(
                {"test1": [sample_raw_jobs[0]], "test2": [sample_raw_jobs[1]]}
            )
            result = pipeline.run_once()

        assert mock_notification_service.send_digest.call_count == 1
        candidates = mock_notification_service.send_digest.call_args.args[0]
        assert len(candidates) == 2
        mock_notification_service.send_notifications.assert_not_called()
        assert [s.notified_count for s in result.source_stats] == [1, 1]
        assert [s.alerts_sent for s in result.source_stats] == [1, 1]
        assert result.total_notified == 2
        assert result.alerts_sent == 2
        assert not result.had_errors

    def test_run_once_digest_empty_when_no_matches(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
    ):
        """No digest is sent when nothing matches."""
        app_config.email.notification_mode = NotificationMode.DIGEST
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

        mock_notification_service.send_digest.assert_not_called()
        assert result.total_notified == 0

    def test_run_once_digest_failure_flags_error_without_discarding_jobs(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """A digest exception is recorded but persisted jobs remain."""
        from app.persistence.database import get_session
        from app.persistence.repositories import JobRepository

        app_config.email.notification_mode = NotificationMode.DIGEST
        mock_notification_service.send_digest.side_effect = Exception("SMTP down")
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_get_adapter.side_effect = self._per_source_adapter(
                {"test1": [sample_raw_jobs[0]], "test2": [sample_raw_jobs[1]]}
            )
            result = pipeline.run_once()

        assert result.had_errors
        assert result.total_errors >= 1
        assert result.total_notified == 0
        with get_session() as session:
            assert len(JobRepository(session).get_by_source("greenhouse", "test1")) == 1

    def test_failed_digest_re_alerts_on_subsequent_run_after_smtp_recovers(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """Matches from a failed digest are re-sent next run, then never again."""
        from app.persistence.database import get_session
        from app.persistence.repositories import AlertRepository

        app_config.email.notification_mode = NotificationMode.DIGEST
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )
        adapter_factory = self._per_source_adapter(
            {"test1": [sample_raw_jobs[0]], "test2": [sample_raw_jobs[1]]}
        )

        def working_send_digest(candidates, env, email, alert_repo, scan_id):
            for c in candidates:
                alert_repo.record_alert(c.job.job_key, c.job.content_hash, utc_now())
            return NotificationResult(
                job_key="digest", version_hash="v", attempts=1, status="sent"
            )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_get_adapter.side_effect = adapter_factory

            # Run 1: SMTP down
            mock_notification_service.send_digest.return_value = NotificationResult(
                job_key="digest", version_hash="", attempts=3, status="failed", error="SMTP down"
            )
            run1 = pipeline.run_once()
            assert run1.had_errors
            with get_session() as session:
                alert_repo = AlertRepository(session)
                keys = [
                    c.job.job_key
                    for c in mock_notification_service.send_digest.call_args.args[0]
                ]
                assert len(keys) == 2
                assert not any(
                    alert_repo.has_been_sent(c.job.job_key, c.job.content_hash)
                    for c in mock_notification_service.send_digest.call_args.args[0]
                )

            # Run 2: SMTP recovered, jobs unchanged
            mock_notification_service.send_digest.reset_mock()
            mock_notification_service.send_digest.side_effect = working_send_digest
            run2 = pipeline.run_once()
            assert not run2.had_errors
            assert mock_notification_service.send_digest.call_count == 1
            candidates = mock_notification_service.send_digest.call_args.args[0]
            assert sorted(c.job.job_key for c in candidates) == sorted(keys)
            assert run2.total_notified == 2
            with get_session() as session:
                alert_repo = AlertRepository(session)
                assert all(
                    alert_repo.has_been_sent(c.job.job_key, c.job.content_hash)
                    for c in candidates
                )

            # Run 3: already alerted, nothing re-sent
            mock_notification_service.send_digest.reset_mock()
            pipeline.run_once()
            mock_notification_service.send_digest.assert_not_called()

    def test_run_once_legacy_mode_sends_per_job_notifications(
        self,
        temp_database,
        app_config,
        env_config,
        mock_notification_service,
        keyword_matcher,
        sample_raw_jobs,
    ):
        """Legacy mode keeps per-source send_notifications and never sends a digest."""
        app_config.email.notification_mode = NotificationMode.LEGACY
        pipeline = ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=mock_notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            mock_get_adapter.side_effect = self._per_source_adapter(
                {"test1": [sample_raw_jobs[0]], "test2": [sample_raw_jobs[1]]}
            )
            pipeline.run_once()

        assert mock_notification_service.send_notifications.call_count == 2
        mock_notification_service.send_digest.assert_not_called()


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
                alerts_sent=3,
                error_count=0,
            ),
            SourceRunStats(
                source_id="source2",
                fetched_count=5,
                normalized_count=5,
                upserted_count=5,
                matched_count=2,
                notified_count=1,
                alerts_sent=1,
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
        assert result.alerts_sent == 4
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


class TestPipelineMatchExplainability:
    """Match Explainability persistence, alert-safe scans and run-state exposure."""

    @staticmethod
    def _matcher():
        from app.config.models import SearchProfile

        return KeywordMatcher(
            [
                SearchProfile(
                    name="py",
                    required_terms=["python"],
                    affinity_weights={"python": 2.0, "senior": 3.0},
                )
            ]
        )

    @staticmethod
    def _raw_job(description="We need a senior Python developer"):
        return RawJob(
            external_id="job1",
            title="Software Engineer",
            company="Tech Corp",
            location="Remote",
            description=description,
            url="https://example.com/jobs/1",
            posted_at=None,
        )

    @staticmethod
    def _make_pipeline(app_config, env_config, notification_service, matcher=None):
        app_config.sources = [app_config.sources[0]]
        app_config.email.notification_mode = NotificationMode.DIGEST
        return ScanPipeline(
            app_config=app_config,
            env_config=env_config,
            notification_service=notification_service,
            keyword_matcher=matcher or TestPipelineMatchExplainability._matcher(),
        )

    @staticmethod
    def _run(pipeline, raw_jobs, **kwargs):
        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            adapter = Mock()
            adapter.fetch_jobs.return_value = raw_jobs
            mock_get_adapter.return_value = adapter
            return pipeline.run_once(**kwargs)

    @staticmethod
    def _stored(job_key=None):
        from app.persistence.database import get_session
        from app.persistence.repositories import JobRepository

        with get_session() as session:
            repo = JobRepository(session)
            if job_key is None:
                jobs = repo.get_by_source("greenhouse", "test1")
                assert len(jobs) == 1
                return jobs[0]
            return repo.get_by_key(job_key)

    def test_run_once_persists_match_explainability_on_jobs_table(
        self, temp_database, app_config, env_config, mock_notification_service
    ):
        pipeline = self._make_pipeline(app_config, env_config, mock_notification_service)

        self._run(pipeline, [self._raw_job()])

        job = self._stored()
        assert job.is_match is True
        assert job.primary_profile == "py"
        assert job.matched_profiles == ["py"]
        assert job.affinity_score > 0
        assert job.score_breakdown
        assert job.match_snippets
        assert job.matched_fields

    def test_run_once_refreshes_match_metadata_on_unchanged_jobs(
        self, temp_database, app_config, env_config, mock_notification_service
    ):
        from app.config.models import SearchProfile
        from app.persistence.database import get_session
        from app.persistence.repositories import JobRepository

        pipeline = self._make_pipeline(app_config, env_config, mock_notification_service)
        self._run(pipeline, [self._raw_job()])
        first = self._stored()
        with get_session() as session:
            JobRepository(session).update_user_status(first.job_key, "saved")

        # Profiles changed between scans; the job content did not
        pipeline.keyword_matcher = KeywordMatcher(
            [SearchProfile(name="renamed", required_terms=["python"], affinity_weights={"python": 9.0})]
        )
        original = JobRepository.update_last_seen_and_match
        with patch.object(
            JobRepository, "update_last_seen_and_match", autospec=True, side_effect=original
        ) as spy:
            result = self._run(pipeline, [self._raw_job()])

        assert spy.call_count == 1
        assert result.total_upserted == 0
        second = self._stored()
        assert second.content_hash == first.content_hash
        assert second.primary_profile == "renamed"
        assert second.affinity_score == 9.0
        assert second.first_seen_at == first.first_seen_at
        assert second.last_seen_at > first.last_seen_at
        assert second.user_status.value == "saved"

    def test_run_once_preserves_user_status_when_job_content_changes(
        self, temp_database, app_config, env_config, mock_notification_service
    ):
        from app.domain.models import TriageStatus
        from app.persistence.database import get_session
        from app.persistence.repositories import JobRepository

        pipeline = self._make_pipeline(app_config, env_config, mock_notification_service)
        self._run(pipeline, [self._raw_job("A python developer")])
        first = self._stored()
        with get_session() as session:
            JobRepository(session).update_user_status(first.job_key, "applied")

        self._run(pipeline, [self._raw_job("A senior python developer")])

        second = self._stored()
        assert second.content_hash != first.content_hash
        assert second.affinity_score > first.affinity_score
        assert second.user_status == TriageStatus.APPLIED

    def test_run_once_alert_safe_mode_skips_notifications_and_preserves_digest_retry(
        self, temp_database, app_config, env_config, mock_notification_service
    ):
        from app.persistence.database import get_session
        from app.persistence.repositories import AlertRepository

        pipeline = self._make_pipeline(app_config, env_config, mock_notification_service)

        def working_send_digest(candidates, env, email, alert_repo, scan_id):
            for c in candidates:
                alert_repo.record_alert(c.job.job_key, c.job.content_hash, utc_now())
            return NotificationResult(
                job_key="digest", version_hash="v", attempts=1, status="sent"
            )

        mock_notification_service.send_digest.side_effect = working_send_digest

        # Alert-safe scan: persists and matches, never notifies
        quiet = self._run(pipeline, [self._raw_job()], send_notifications=False)
        job = self._stored()
        assert quiet.total_matched > 0
        assert quiet.total_notified == 0
        assert quiet.alerts_sent == 0
        mock_notification_service.send_digest.assert_not_called()
        mock_notification_service.send_notifications.assert_not_called()
        assert job.is_match is True
        with get_session() as session:
            assert AlertRepository(session).get_alerts_for_job(job.job_key) == []

        # Scheduled scan: unchanged job is picked up as a Digest Retry
        loud = self._run(pipeline, [self._raw_job()])
        assert loud.total_notified == 1
        assert loud.alerts_sent == 1
        candidates = mock_notification_service.send_digest.call_args.args[0]
        assert [c.is_retry for c in candidates] == [True]
        assert [c.needs_alert for c in candidates] == [True]
        with get_session() as session:
            assert AlertRepository(session).has_been_sent(job.job_key, job.content_hash)

    def test_alert_safe_mode_skips_legacy_notifications(
        self, temp_database, app_config, env_config, mock_notification_service
    ):
        pipeline = self._make_pipeline(app_config, env_config, mock_notification_service)
        app_config.email.notification_mode = NotificationMode.LEGACY

        result = self._run(pipeline, [self._raw_job()], send_notifications=False)

        assert result.total_matched > 0
        assert result.total_notified == 0
        mock_notification_service.send_notifications.assert_not_called()
        mock_notification_service.send_digest.assert_not_called()

    def test_pipeline_tracks_last_run_result_and_is_running_property(
        self, temp_database, app_config, env_config, mock_notification_service
    ):
        pipeline = self._make_pipeline(app_config, env_config, mock_notification_service)
        assert pipeline.last_run_result is None
        assert pipeline.is_running is False

        observed = []
        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter:
            adapter = Mock()

            def fetch(*args, **kwargs):
                observed.append(pipeline.is_running)
                # A concurrent trigger is skipped and must not replace last_run_result
                observed.append(pipeline.run_once().skipped)
                return [self._raw_job()]

            adapter.fetch_jobs.side_effect = fetch
            mock_get_adapter.return_value = adapter
            result = pipeline.run_once()

        assert observed == [True, True]
        assert pipeline.is_running is False
        assert pipeline.last_run_result is result
        assert result.skipped is False
