"""Integration tests for pipeline with notifications.

Tests end-to-end flow:
- Adapter → Normalization → Matching → Notifications
- Alert deduplication across multiple runs
- Multiple sources with partial failures
- Real SQLite database (in-memory)
- Mocked SMTP for notifications
"""

from unittest.mock import Mock, patch

import pytest

from app.config.environment import EnvironmentConfig
from app.config.models import (
    AdvancedConfig,
    AppConfig,
    EmailConfig,
    SearchCriteria,
    SourceConfig,
)
from app.domain.models import RawJob
from app.matching.engine import KeywordMatcher
from app.notifications.models import NotificationResult
from app.notifications.service import NotificationService
from app.persistence.database import close_database, get_session, init_database
from app.persistence.repositories import AlertRepository, JobRepository
from app.pipeline import ScanPipeline
from app.utils.timestamps import utc_now


@pytest.fixture
def integration_database():
    """Create an in-memory database for integration testing."""
    init_database("sqlite:///:memory:")
    yield
    close_database()


@pytest.fixture
def integration_app_config():
    """App configuration for integration tests."""
    return AppConfig(
        sources=[
            SourceConfig(
                name="Test Greenhouse",
                type="greenhouse",
                identifier="testco",
                enabled=True,
            ),
            SourceConfig(
                name="Test Lever",
                type="lever",
                identifier="startup",
                enabled=True,
            ),
        ],
        search_criteria=SearchCriteria(
            required_terms=["python", "engineer"],
            keyword_groups=[["senior", "lead", "staff"]],
            exclude_terms=["contract", "intern"],
        ),
        email=EmailConfig(
            sender_email="jobs@example.com",
            subject_prefix="[Job Alert]",
        ),
        scan_interval="15m",
        scan_interval_seconds=900,
        advanced=AdvancedConfig(),
    )


@pytest.fixture
def integration_env_config():
    """Environment configuration for integration tests."""
    return EnvironmentConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="test@example.com",
        smtp_pass="password",
        alert_to_email="alerts@example.com",
        database_url="sqlite:///:memory:",
    )


@pytest.fixture
def sample_matching_jobs():
    """Sample jobs that match the search criteria."""
    return [
        RawJob(
            external_id="job1",
            title="Senior Python Engineer",
            company="Tech Corp",
            location="Remote",
            description="Looking for a senior Python engineer with 5+ years experience. "
            "You'll work on distributed systems and lead technical initiatives.",
            url="https://example.com/jobs/1",
            posted_at=None,
        ),
        RawJob(
            external_id="job2",
            title="Lead Python Engineer",
            company="Startup Inc",
            location="San Francisco",
            description="We need a lead Python engineer to build our core platform. "
            "Experience with AWS and microservices required.",
            url="https://example.com/jobs/2",
            posted_at=None,
        ),
    ]


@pytest.fixture
def sample_non_matching_jobs():
    """Sample jobs that don't match the search criteria."""
    return [
        RawJob(
            external_id="job3",
            title="Python Intern",
            company="Big Corp",
            location="New York",
            description="Internship opportunity for Python development. Learn from experienced engineers.",
            url="https://example.com/jobs/3",
            posted_at=None,
        ),
        RawJob(
            external_id="job4",
            title="Java Developer",
            company="Enterprise Co",
            location="Austin",
            description="Java developer needed for enterprise applications.",
            url="https://example.com/jobs/4",
            posted_at=None,
        ),
    ]


class TestPipelineNotificationsIntegration:
    """Integration tests for pipeline with notifications."""

    def test_end_to_end_flow_with_matching_jobs(
        self,
        integration_database,
        integration_app_config,
        integration_env_config,
        sample_matching_jobs,
    ):
        """Test complete flow from adapter to notifications."""
        # Setup mocks
        notification_service = NotificationService()
        keyword_matcher = KeywordMatcher(integration_app_config.search_criteria)

        pipeline = ScanPipeline(
            app_config=integration_app_config,
            env_config=integration_env_config,
            notification_service=notification_service,
            keyword_matcher=keyword_matcher,
        )

        # Mock adapters and SMTP
        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter, patch(
            "app.notifications.smtp_client.SMTPClient.send_email"
        ) as mock_send_email:

            # Setup adapter mock
            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = sample_matching_jobs
            mock_get_adapter.return_value = mock_adapter

            # Setup SMTP mock
            mock_send_email.return_value = True

            # Run pipeline
            result = pipeline.run_once()

            # Verify results
            assert result.total_fetched == 4  # 2 jobs * 2 sources
            assert result.total_normalized == 4
            assert result.total_upserted == 4
            assert result.total_matched == 4  # All jobs match criteria
            assert result.total_notified == 4  # All matches notified
            assert not result.had_errors

            # Verify emails were sent
            assert mock_send_email.call_count == 4

    def test_alert_deduplication_across_runs(
        self,
        integration_database,
        integration_app_config,
        integration_env_config,
        sample_matching_jobs,
    ):
        """Test that alerts are not sent twice for the same job."""
        notification_service = NotificationService()
        keyword_matcher = KeywordMatcher(integration_app_config.search_criteria)

        pipeline = ScanPipeline(
            app_config=integration_app_config,
            env_config=integration_env_config,
            notification_service=notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter, patch(
            "app.notifications.smtp_client.SMTPClient.send_email"
        ) as mock_send_email:

            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = sample_matching_jobs
            mock_get_adapter.return_value = mock_adapter
            mock_send_email.return_value = True

            # First run - should send notifications
            result1 = pipeline.run_once()
            assert result1.total_notified == 4
            first_run_emails = mock_send_email.call_count

            # Second run - same jobs, should not send notifications (duplicates)
            result2 = pipeline.run_once()
            assert result2.total_notified == 0  # No new notifications
            second_run_emails = mock_send_email.call_count - first_run_emails
            assert second_run_emails == 0

    def test_updated_job_triggers_new_notification(
        self,
        integration_database,
        integration_app_config,
        integration_env_config,
        sample_matching_jobs,
    ):
        """Test that updated job content triggers a new notification."""
        notification_service = NotificationService()
        keyword_matcher = KeywordMatcher(integration_app_config.search_criteria)

        pipeline = ScanPipeline(
            app_config=integration_app_config,
            env_config=integration_env_config,
            notification_service=notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter, patch(
            "app.notifications.smtp_client.SMTPClient.send_email"
        ) as mock_send_email:

            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = sample_matching_jobs
            mock_get_adapter.return_value = mock_adapter
            mock_send_email.return_value = True

            # First run
            result1 = pipeline.run_once()
            first_run_emails = mock_send_email.call_count

            # Update job description
            updated_jobs = [
                RawJob(
                    external_id="job1",
                    title="Senior Python Engineer",
                    company="Tech Corp",
                    location="Remote",
                    description="UPDATED: Now looking for a staff-level senior Python engineer. "
                    "Salary range increased!",
                    url="https://example.com/jobs/1",
                    posted_at=None,
                ),
            ] + sample_matching_jobs[1:]

            mock_adapter.fetch_jobs.return_value = updated_jobs

            # Second run with updated job
            result2 = pipeline.run_once()
            second_run_emails = mock_send_email.call_count - first_run_emails

            # Should send notification for updated job (at least 2 - one per source)
            assert second_run_emails >= 2

    def test_multiple_sources_with_partial_failure(
        self,
        integration_database,
        integration_app_config,
        integration_env_config,
        sample_matching_jobs,
    ):
        """Test that one source failing doesn't affect others."""
        from app.adapters.exceptions import AdapterHTTPError

        notification_service = NotificationService()
        keyword_matcher = KeywordMatcher(integration_app_config.search_criteria)

        pipeline = ScanPipeline(
            app_config=integration_app_config,
            env_config=integration_env_config,
            notification_service=notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter, patch(
            "app.notifications.smtp_client.SMTPClient.send_email"
        ) as mock_send_email:

            mock_adapter = Mock()

            # First source succeeds, second fails
            def side_effect(source_config):
                if source_config.identifier == "testco":
                    mock_adapter.fetch_jobs.return_value = sample_matching_jobs
                else:
                    mock_adapter.fetch_jobs.side_effect = AdapterHTTPError(
                        "Service unavailable", status_code=503
                    )
                return mock_adapter

            mock_get_adapter.side_effect = side_effect
            mock_send_email.return_value = True

            result = pipeline.run_once()

            # Should have processed both sources
            assert len(result.source_stats) == 2
            assert result.had_errors

            # First source should succeed
            testco_stats = next(s for s in result.source_stats if s.source_id == "testco")
            assert testco_stats.fetched_count == 2
            assert not testco_stats.had_errors

            # Second source should fail
            startup_stats = next(s for s in result.source_stats if s.source_id == "startup")
            assert startup_stats.had_errors
            assert startup_stats.fetched_count == 0

            # Should still send notifications for successful source
            assert result.total_notified == 2

    def test_no_notifications_for_non_matching_jobs(
        self,
        integration_database,
        integration_app_config,
        integration_env_config,
        sample_non_matching_jobs,
    ):
        """Test that jobs not matching criteria don't trigger notifications."""
        notification_service = NotificationService()
        keyword_matcher = KeywordMatcher(integration_app_config.search_criteria)

        pipeline = ScanPipeline(
            app_config=integration_app_config,
            env_config=integration_env_config,
            notification_service=notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter, patch(
            "app.notifications.smtp_client.SMTPClient.send_email"
        ) as mock_send_email:

            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = sample_non_matching_jobs
            mock_get_adapter.return_value = mock_adapter
            mock_send_email.return_value = True

            result = pipeline.run_once()

            # Jobs should be processed but not matched
            assert result.total_fetched == 4
            assert result.total_normalized == 4
            assert result.total_matched == 0
            assert result.total_notified == 0

            # No emails should be sent
            mock_send_email.assert_not_called()

    def test_jobs_persisted_to_database(
        self,
        integration_database,
        integration_app_config,
        integration_env_config,
        sample_matching_jobs,
    ):
        """Test that jobs are correctly persisted to database."""
        notification_service = NotificationService()
        keyword_matcher = KeywordMatcher(integration_app_config.search_criteria)

        pipeline = ScanPipeline(
            app_config=integration_app_config,
            env_config=integration_env_config,
            notification_service=notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter, patch(
            "app.notifications.smtp_client.SMTPClient.send_email"
        ) as mock_send_email:

            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = sample_matching_jobs
            mock_get_adapter.return_value = mock_adapter
            mock_send_email.return_value = True

            # Run pipeline
            pipeline.run_once()

            # Check database
            with get_session() as session:
                job_repo = JobRepository(session)
                alert_repo = AlertRepository(session)

                # Should have 4 jobs (2 sources * 2 jobs)
                all_jobs = job_repo.get_all_active()
                assert len(all_jobs) >= 4

                # Should have 4 alert records
                # Query alert records to verify they exist
                all_alerts = alert_repo.get_recent_alerts(limit=100)
                assert len(all_alerts) >= 4

    def test_smtp_failure_doesnt_crash_pipeline(
        self,
        integration_database,
        integration_app_config,
        integration_env_config,
        sample_matching_jobs,
    ):
        """Test that SMTP failures are handled gracefully."""
        notification_service = NotificationService()
        keyword_matcher = KeywordMatcher(integration_app_config.search_criteria)

        pipeline = ScanPipeline(
            app_config=integration_app_config,
            env_config=integration_env_config,
            notification_service=notification_service,
            keyword_matcher=keyword_matcher,
        )

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter, patch(
            "app.notifications.smtp_client.SMTPClient.send_email"
        ) as mock_send_email:

            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = sample_matching_jobs
            mock_get_adapter.return_value = mock_adapter

            # SMTP fails
            mock_send_email.side_effect = Exception("SMTP connection refused")

            result = pipeline.run_once()

            # Pipeline should complete
            assert not result.skipped
            assert result.total_fetched == 4
            assert result.had_errors  # SMTP error flagged

    def test_mixed_matching_and_non_matching_jobs(
        self,
        integration_database,
        integration_app_config,
        integration_env_config,
        sample_matching_jobs,
        sample_non_matching_jobs,
    ):
        """Test processing mix of matching and non-matching jobs."""
        notification_service = NotificationService()
        keyword_matcher = KeywordMatcher(integration_app_config.search_criteria)

        pipeline = ScanPipeline(
            app_config=integration_app_config,
            env_config=integration_env_config,
            notification_service=notification_service,
            keyword_matcher=keyword_matcher,
        )

        mixed_jobs = sample_matching_jobs[:1] + sample_non_matching_jobs[:1]

        with patch("app.pipeline.runner.get_adapter") as mock_get_adapter, patch(
            "app.notifications.smtp_client.SMTPClient.send_email"
        ) as mock_send_email:

            mock_adapter = Mock()
            mock_adapter.fetch_jobs.return_value = mixed_jobs
            mock_get_adapter.return_value = mock_adapter
            mock_send_email.return_value = True

            result = pipeline.run_once()

            # Should process all jobs
            assert result.total_fetched == 4  # 2 jobs * 2 sources
            assert result.total_normalized == 4

            # Only matching jobs should trigger notifications
            assert result.total_matched == 2  # 1 matching job * 2 sources
            assert result.total_notified == 2

            # Should send 2 emails
            assert mock_send_email.call_count == 2
