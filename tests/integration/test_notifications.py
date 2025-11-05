"""Integration tests for the notification service.

Tests the complete notification pipeline from candidate match through
to email delivery and alert persistence, using in-memory database
and mocked SMTP.
"""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.config.environment import EnvironmentConfig
from app.config.models import EmailConfig
from app.domain.models import Job
from app.matching.models import CandidateMatch, MatchResult
from app.normalization.models import NormalizationResult
from app.notifications import NotificationService
from app.persistence import close_database, get_session, init_database
from app.persistence.repositories import AlertRepository
from app.utils.timestamps import utc_now


def create_norm_result(job, is_new=False, content_changed=True):
    """Helper to create NormalizationResult with all required fields."""
    from app.domain.models import RawJob
    from app.normalization.models import MatchableText

    raw_job = RawJob(
        external_id=job.external_id,
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        url=job.url,
        posted_at=job.posted_at,
        updated_at=job.updated_at,
    )

    matchable_text = MatchableText.from_job(job)

    return NormalizationResult(
        job=job,
        existing_job=None,
        is_new=is_new,
        content_changed=content_changed,
        matchable_text=matchable_text,
        raw_job=raw_job,
    )


@pytest.fixture
def db_session(tmp_path):
    """Create test database session."""
    db_file = tmp_path / "test_notifications.db"
    db_url = f"sqlite:///{db_file}"
    init_database(db_url)
    session = get_session()
    yield session
    session.close()
    close_database()


@pytest.fixture
def alert_repo(db_session):
    """Create alert repository with test session."""
    return AlertRepository(db_session)


@pytest.fixture
def email_config():
    """Email configuration for testing."""
    return EmailConfig(
        use_tls=True,
        max_retries=2,
        retry_backoff_multiplier=2.0,
        retry_initial_delay=1,
    )


@pytest.fixture
def env_config():
    """Environment configuration for testing."""
    return EnvironmentConfig(
        smtp_host="smtp.test.com",
        smtp_port=587,
        smtp_user="test@test.com",
        smtp_pass="testpass",
        alert_to_email="recipient@test.com",
        smtp_sender_name="Test Scanner",
    )


@pytest.fixture
def sample_candidate():
    """Create a sample candidate match."""
    job = Job(
        job_key="integration_test_job_123",
        source_type="greenhouse",
        source_identifier="testcorp",
        external_id="ext_789",
        title="Integration Test Engineer",
        company="Integration Corp",
        location="Remote",
        description="Python integration testing position with pytest experience.",
        url="https://test.com/job/789",
        posted_at=datetime(2025, 11, 1, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2025, 11, 2, 14, 0, 0, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 11, 3, 8, 0, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2025, 11, 4, 9, 0, 0, tzinfo=timezone.utc),
        content_hash="integration_content_hash",
    )

    match_result = MatchResult(
        is_match=True,
        matched_required_terms={"python", "pytest"},
        missing_required_terms=set(),
        matched_keyword_groups=[{"integration"}],
        missing_keyword_groups=[],
        matched_exclude_terms=set(),
        matched_fields={
            "title": {"integration"},
            "description": {"python", "pytest", "integration"},
        },
        snippets=[
            "Python integration testing position with pytest experience."
        ],
        summary="Matched required: python, pytest\nMatched groups: integration",
    )

    norm_result = create_norm_result(job, is_new=True, content_changed=True)

    return CandidateMatch(
        normalization_result=norm_result,
        match_result=match_result,
    )


def test_full_notification_flow_with_database(
    sample_candidate, alert_repo, email_config, env_config, db_session
):
    """Test complete notification flow with database integration."""
    # Mock SMTP to avoid actual email sending
    mock_smtp = Mock()
    mock_smtp.send.return_value = None  # Success

    # Create service with mocked SMTP
    service = NotificationService(smtp_client=mock_smtp)

    # Send notification
    result = service.send_candidate_match(
        sample_candidate, env_config, email_config, alert_repo
    )

    # Verify result
    assert result.status == "sent"
    assert result.attempts == 1
    assert result.is_success()
    assert result.should_record_alert()

    # Verify SMTP was called
    mock_smtp.send.assert_called_once()

    # Verify alert was recorded in database
    db_session.commit()  # Commit the transaction
    assert alert_repo.has_been_sent(
        "integration_test_job_123", "integration_content_hash"
    )

    # Verify alert record details
    alerts = alert_repo.get_alerts_for_job("integration_test_job_123")
    assert len(alerts) == 1
    assert alerts[0].job_key == "integration_test_job_123"
    assert alerts[0].version_hash == "integration_content_hash"


def test_duplicate_notification_prevented_by_database(
    sample_candidate, alert_repo, email_config, env_config, db_session
):
    """Test that duplicate notifications are prevented via database check."""
    # Mock SMTP
    mock_smtp = Mock()
    mock_smtp.send.return_value = None

    service = NotificationService(smtp_client=mock_smtp)

    # Send first notification
    result1 = service.send_candidate_match(
        sample_candidate, env_config, email_config, alert_repo
    )
    db_session.commit()

    assert result1.status == "sent"
    assert mock_smtp.send.call_count == 1

    # Try to send again with same job/version
    result2 = service.send_candidate_match(
        sample_candidate, env_config, email_config, alert_repo
    )

    # Should be detected as duplicate
    assert result2.status == "duplicate"
    assert result2.attempts == 0

    # SMTP should not be called again
    assert mock_smtp.send.call_count == 1  # Still just 1

    # Should still have only 1 alert record
    alerts = alert_repo.get_alerts_for_job("integration_test_job_123")
    assert len(alerts) == 1


def test_content_change_triggers_new_notification(
    sample_candidate, alert_repo, email_config, env_config, db_session
):
    """Test that content changes trigger new notifications."""
    # Mock SMTP
    mock_smtp = Mock()
    mock_smtp.send.return_value = None

    service = NotificationService(smtp_client=mock_smtp)

    # Send first notification
    result1 = service.send_candidate_match(
        sample_candidate, env_config, email_config, alert_repo
    )
    db_session.commit()

    assert result1.status == "sent"

    # Create updated version with different content
    updated_job = Job(
        job_key="integration_test_job_123",  # Same job key
        source_type="greenhouse",
        source_identifier="testcorp",
        external_id="ext_789",
        title="Integration Test Engineer - UPDATED",  # Changed
        company="Integration Corp",
        location="Remote",
        description="Updated description with new requirements.",  # Changed
        url="https://test.com/job/789",
        posted_at=datetime(2025, 11, 1, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2025, 11, 5, 16, 0, 0, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 11, 3, 8, 0, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2025, 11, 5, 16, 0, 0, tzinfo=timezone.utc),
        content_hash="different_content_hash",  # Different hash
    )

    updated_match_result = MatchResult(
        is_match=True,
        matched_required_terms={"python"},
        snippets=["Updated description"],
        summary="Updated match",
    )

    updated_norm_result = create_norm_result(updated_job, is_new=False, content_changed=True)

    updated_candidate = CandidateMatch(
        normalization_result=updated_norm_result,
        match_result=updated_match_result,
    )

    # Send notification for updated version
    result2 = service.send_candidate_match(
        updated_candidate, env_config, email_config, alert_repo
    )
    db_session.commit()

    # Should send (different content hash)
    assert result2.status == "sent"
    assert mock_smtp.send.call_count == 2

    # Should have 2 alert records (different versions)
    alerts = alert_repo.get_alerts_for_job("integration_test_job_123")
    assert len(alerts) == 2

    # Verify both versions recorded
    version_hashes = {alert.version_hash for alert in alerts}
    assert "integration_content_hash" in version_hashes
    assert "different_content_hash" in version_hashes


def test_batch_notification_with_database(
    alert_repo, email_config, env_config, db_session
):
    """Test batch notification processing with database."""
    # Create multiple candidates
    candidates = []
    for i in range(3):
        job = Job(
            job_key=f"batch_job_{i}",
            source_type="greenhouse",
            source_identifier="batchcorp",
            external_id=f"batch_ext_{i}",
            title=f"Batch Job {i}",
            company="Batch Corp",
            location="Remote",
            description=f"Batch test job {i}",
            url=f"https://test.com/batch/{i}",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash=f"batch_hash_{i}",
        )

        match_result = MatchResult(
            is_match=True,
            matched_required_terms={"batch"},
            snippets=[f"Batch test job {i}"],
            summary=f"Batch match {i}",
        )

        norm_result = create_norm_result(job, is_new=True, content_changed=True)

        candidates.append(
            CandidateMatch(
                normalization_result=norm_result,
                match_result=match_result,
            )
        )

    # Mock SMTP
    mock_smtp = Mock()
    mock_smtp.send.return_value = None

    service = NotificationService(smtp_client=mock_smtp)

    # Send batch
    results = service.send_notifications(
        candidates, env_config, email_config, alert_repo
    )
    db_session.commit()

    # Verify all sent
    assert len(results) == 3
    assert all(r.status == "sent" for r in results)
    assert mock_smtp.send.call_count == 3

    # Verify all recorded in database
    for i in range(3):
        assert alert_repo.has_been_sent(f"batch_job_{i}", f"batch_hash_{i}")


def test_notification_failure_does_not_record_alert(
    sample_candidate, alert_repo, email_config, env_config, db_session
):
    """Test that failed notifications don't record alerts."""
    # Mock SMTP to fail
    mock_smtp = Mock()
    from app.notifications.models import SMTPDeliveryError

    mock_smtp.send.side_effect = SMTPDeliveryError("SMTP failure")

    service = NotificationService(smtp_client=mock_smtp)

    # Import patch for time.sleep
    from unittest.mock import patch

    with patch("time.sleep"):
        result = service.send_candidate_match(
            sample_candidate, env_config, email_config, alert_repo
        )

    # Should fail
    assert result.status == "failed"
    assert not result.should_record_alert()

    # Alert should NOT be recorded
    db_session.commit()
    assert not alert_repo.has_been_sent(
        "integration_test_job_123", "integration_content_hash"
    )

    # No alert records should exist
    alerts = alert_repo.get_alerts_for_job("integration_test_job_123")
    assert len(alerts) == 0
