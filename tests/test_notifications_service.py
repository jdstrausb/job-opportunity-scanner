"""Unit tests for notification service.

Tests the NotificationService for:
- Complete notification flow
- Deduplication checking
- Skip logic (should_notify, content_changed)
- Template rendering integration
- SMTP delivery with retry/backoff
- Alert repository coordination
- Error handling
- Batch processing
"""

from datetime import datetime, timezone
from unittest.mock import ANY, MagicMock, Mock, patch

import pytest

from app.config.environment import EnvironmentConfig
from app.config.models import EmailConfig
from app.domain.models import Job
from app.matching.models import CandidateMatch, MatchResult
from app.normalization.models import NormalizationResult
from app.notifications.models import (
    NotificationResult,
    NotificationTemplateError,
    SMTPDeliveryError,
)
from app.notifications.service import NotificationService
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
def email_config():
    """Email configuration for testing."""
    return EmailConfig(
        use_tls=True,
        max_retries=3,
        retry_backoff_multiplier=2.0,
        retry_initial_delay=1,  # Short delay for tests
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
def sample_job():
    """Sample job for testing."""
    return Job(
        job_key="test_job_123",
        source_type="greenhouse",
        source_identifier="testcorp",
        external_id="ext_456",
        title="Senior Python Engineer",
        company="Test Corp",
        location="Remote",
        description="Python engineer position with AWS experience.",
        url="https://test.com/job/456",
        posted_at=datetime(2025, 11, 1, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2025, 11, 2, 14, 0, 0, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 11, 3, 8, 0, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2025, 11, 4, 9, 0, 0, tzinfo=timezone.utc),
        content_hash="content_abc123",
    )


@pytest.fixture
def sample_match_result():
    """Sample match result."""
    return MatchResult(
        is_match=True,
        matched_required_terms={"python"},
        missing_required_terms=set(),
        matched_keyword_groups=[{"senior"}],
        missing_keyword_groups=[],
        matched_exclude_terms=set(),
        matched_fields={"title": {"python", "senior"}},
        snippets=["Python engineer position"],
        summary="Matched terms: python, senior",
    )


@pytest.fixture
def candidate_should_notify(sample_job, sample_match_result):
    """Candidate that should trigger notification."""
    norm_result = create_norm_result(sample_job, is_new=False, content_changed=True)
    return CandidateMatch(
        normalization_result=norm_result,
        match_result=sample_match_result,
    )


@pytest.fixture
def candidate_no_content_change(sample_job, sample_match_result):
    """Candidate with no content change."""
    norm_result = create_norm_result(sample_job, is_new=False, content_changed=False)
    return CandidateMatch(
        normalization_result=norm_result,
        match_result=sample_match_result,
    )


@pytest.fixture
def candidate_excluded(sample_job):
    """Candidate that is excluded."""
    match_result = MatchResult(
        is_match=False,
        matched_exclude_terms={"contract"},  # Has exclude term
        snippets=[],
        summary="Excluded",
    )
    norm_result = create_norm_result(sample_job, is_new=False, content_changed=True)
    return CandidateMatch(
        normalization_result=norm_result,
        match_result=match_result,
    )


def test_notification_service_initialization():
    """Test NotificationService initialization."""
    service = NotificationService()

    assert service is not None
    assert service.template_renderer is not None
    assert service.smtp_client is not None


def test_send_candidate_match_success(
    candidate_should_notify, email_config, env_config
):
    """Test successful notification send."""
    # Mock dependencies
    mock_renderer = Mock()
    mock_renderer.render.return_value = {
        "subject": "Test Subject",
        "html_body": "<html>Test</html>",
        "text_body": "Test",
    }

    mock_smtp = Mock()
    mock_smtp.send.return_value = None  # Success

    mock_alert_repo = Mock()
    mock_alert_repo.has_been_sent.return_value = False  # Not sent before
    mock_alert_repo.record_alert.return_value = None

    service = NotificationService(
        template_renderer=mock_renderer,
        smtp_client=mock_smtp,
    )

    result = service.send_candidate_match(
        candidate_should_notify, env_config, email_config, mock_alert_repo
    )

    # Should be successful
    assert result.status == "sent"
    assert result.attempts == 1
    assert result.is_success()
    assert result.should_record_alert()

    # Should check for duplicate
    mock_alert_repo.has_been_sent.assert_called_once_with(
        "test_job_123", "content_abc123"
    )

    # Should render templates
    mock_renderer.render.assert_called_once()

    # Should send via SMTP
    mock_smtp.send.assert_called_once()

    # Should record alert (timestamp is close to now)
    mock_alert_repo.record_alert.assert_called_once_with(
        "test_job_123", "content_abc123", ANY
    )


def test_send_candidate_match_skipped_should_not_notify(
    candidate_excluded, email_config, env_config
):
    """Test that notification is skipped when should_notify is False."""
    mock_alert_repo = Mock()

    service = NotificationService()

    result = service.send_candidate_match(
        candidate_excluded, env_config, email_config, mock_alert_repo
    )

    # Should be skipped
    assert result.status == "skipped"
    assert result.attempts == 0
    assert not result.should_record_alert()

    # Should not check duplicate or send
    mock_alert_repo.has_been_sent.assert_not_called()
    mock_alert_repo.record_alert.assert_not_called()


def test_send_candidate_match_skipped_no_content_change(
    candidate_no_content_change, email_config, env_config
):
    """Test that notification is skipped when content hasn't changed."""
    mock_alert_repo = Mock()

    service = NotificationService()

    result = service.send_candidate_match(
        candidate_no_content_change, env_config, email_config, mock_alert_repo
    )

    # Should be skipped
    assert result.status == "skipped"
    assert result.attempts == 0

    # Should not proceed to send
    mock_alert_repo.has_been_sent.assert_not_called()


def test_send_candidate_match_duplicate_detection(
    candidate_should_notify, email_config, env_config
):
    """Test that duplicate alerts are detected and skipped."""
    mock_alert_repo = Mock()
    mock_alert_repo.has_been_sent.return_value = True  # Already sent

    service = NotificationService()

    result = service.send_candidate_match(
        candidate_should_notify, env_config, email_config, mock_alert_repo
    )

    # Should be marked as duplicate
    assert result.status == "duplicate"
    assert result.attempts == 0
    assert not result.should_record_alert()

    # Should check duplicate but not send
    mock_alert_repo.has_been_sent.assert_called_once()
    mock_alert_repo.record_alert.assert_not_called()


def test_send_candidate_match_template_error(
    candidate_should_notify, email_config, env_config
):
    """Test handling of template rendering errors."""
    mock_renderer = Mock()
    mock_renderer.render.side_effect = NotificationTemplateError("Template error")

    mock_alert_repo = Mock()
    mock_alert_repo.has_been_sent.return_value = False

    service = NotificationService(template_renderer=mock_renderer)

    result = service.send_candidate_match(
        candidate_should_notify, env_config, email_config, mock_alert_repo
    )

    # Should fail without retry (template errors are fatal)
    assert result.status == "failed"
    assert result.attempts == 0
    assert "Template rendering failed" in result.error
    assert not result.should_record_alert()

    # Should not record alert on failure
    mock_alert_repo.record_alert.assert_not_called()


def test_send_candidate_match_smtp_retry_then_success(
    candidate_should_notify, email_config, env_config
):
    """Test SMTP retry logic with eventual success."""
    mock_renderer = Mock()
    mock_renderer.render.return_value = {
        "subject": "Test",
        "html_body": "<html>Test</html>",
        "text_body": "Test",
    }

    mock_smtp = Mock()
    # Fail twice, then succeed
    mock_smtp.send.side_effect = [
        SMTPDeliveryError("Temporary failure"),
        SMTPDeliveryError("Temporary failure"),
        None,  # Success on third attempt
    ]

    mock_alert_repo = Mock()
    mock_alert_repo.has_been_sent.return_value = False

    service = NotificationService(
        template_renderer=mock_renderer,
        smtp_client=mock_smtp,
    )

    # Use short delays for testing
    with patch("time.sleep"):
        result = service.send_candidate_match(
            candidate_should_notify, env_config, email_config, mock_alert_repo
        )

    # Should succeed after retries
    assert result.status == "sent"
    assert result.attempts == 3
    assert result.is_success()

    # Should record alert
    mock_alert_repo.record_alert.assert_called_once()


def test_send_candidate_match_smtp_all_retries_exhausted(
    candidate_should_notify, email_config, env_config
):
    """Test SMTP retry exhaustion."""
    mock_renderer = Mock()
    mock_renderer.render.return_value = {
        "subject": "Test",
        "html_body": "<html>Test</html>",
        "text_body": "Test",
    }

    mock_smtp = Mock()
    # Always fail
    mock_smtp.send.side_effect = SMTPDeliveryError("Permanent failure")

    mock_alert_repo = Mock()
    mock_alert_repo.has_been_sent.return_value = False

    service = NotificationService(
        template_renderer=mock_renderer,
        smtp_client=mock_smtp,
    )

    with patch("time.sleep"):
        result = service.send_candidate_match(
            candidate_should_notify, env_config, email_config, mock_alert_repo
        )

    # Should fail after all retries
    assert result.status == "failed"
    assert result.attempts == 4  # max_retries=3 + 1 initial attempt
    assert "Permanent failure" in result.error
    assert not result.should_record_alert()

    # Should not record alert
    mock_alert_repo.record_alert.assert_not_called()


def test_send_notifications_batch_processing(email_config, env_config):
    """Test batch processing of multiple candidates."""
    # Create multiple candidates
    candidates = []
    for i in range(3):
        job = Job(
            job_key=f"job_{i}",
            source_type="greenhouse",
            source_identifier="test",
            external_id=f"ext_{i}",
            title=f"Job {i}",
            company="Test Corp",
            location="Remote",
            description="Test description",
            url=f"https://test.com/{i}",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash=f"hash_{i}",
        )

        match_result = MatchResult(
            is_match=True,
            matched_required_terms={"test"},
            snippets=["Test"],
            summary="Test match",
        )

        norm_result = create_norm_result(job, is_new=True, content_changed=True)

        candidates.append(
            CandidateMatch(
                normalization_result=norm_result,
                match_result=match_result,
            )
        )

    # Mock dependencies
    mock_renderer = Mock()
    mock_renderer.render.return_value = {
        "subject": "Test",
        "html_body": "<html>Test</html>",
        "text_body": "Test",
    }

    mock_smtp = Mock()
    mock_smtp.send.return_value = None

    mock_alert_repo = Mock()
    mock_alert_repo.has_been_sent.return_value = False

    service = NotificationService(
        template_renderer=mock_renderer,
        smtp_client=mock_smtp,
    )

    results = service.send_notifications(
        candidates, env_config, email_config, mock_alert_repo
    )

    # Should process all candidates
    assert len(results) == 3
    assert all(r.status == "sent" for r in results)

    # Should send 3 emails
    assert mock_smtp.send.call_count == 3

    # Should record 3 alerts
    assert mock_alert_repo.record_alert.call_count == 3


def test_send_notifications_continues_on_individual_failure(
    email_config, env_config
):
    """Test that batch processing continues even if individual sends fail."""
    # Create 3 candidates
    candidates = []
    for i in range(3):
        job = Job(
            job_key=f"job_{i}",
            source_type="greenhouse",
            source_identifier="test",
            external_id=f"ext_{i}",
            title=f"Job {i}",
            company="Test Corp",
            location="Remote",
            description="Test",
            url=f"https://test.com/{i}",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash=f"hash_{i}",
        )

        match_result = MatchResult(
            is_match=True,
            matched_required_terms={"test"},
            snippets=["Test"],
            summary="Test",
        )

        norm_result = create_norm_result(job, is_new=True, content_changed=True)

        candidates.append(
            CandidateMatch(
                normalization_result=norm_result,
                match_result=match_result,
            )
        )

    mock_renderer = Mock()
    mock_renderer.render.return_value = {
        "subject": "Test",
        "html_body": "<html>Test</html>",
        "text_body": "Test",
    }

    mock_smtp = Mock()
    # Fail on second send (with retries - max_retries=3 means 4 total attempts)
    mock_smtp.send.side_effect = [
        None,  # First succeeds
        SMTPDeliveryError("Failed"),  # Second attempt 1
        SMTPDeliveryError("Failed"),  # Second attempt 2
        SMTPDeliveryError("Failed"),  # Second attempt 3
        SMTPDeliveryError("Failed"),  # Second attempt 4 (final)
        None,  # Third succeeds
    ]

    mock_alert_repo = Mock()
    mock_alert_repo.has_been_sent.return_value = False

    service = NotificationService(
        template_renderer=mock_renderer,
        smtp_client=mock_smtp,
    )

    with patch("time.sleep"):
        results = service.send_notifications(
            candidates, env_config, email_config, mock_alert_repo
        )

    # Should have 3 results
    assert len(results) == 3

    # First and third should succeed, second should fail
    assert results[0].status == "sent"
    assert results[1].status == "failed"
    assert results[2].status == "sent"


def test_send_notifications_logs_summary(email_config, env_config, caplog):
    """Test that batch processing logs summary statistics."""
    candidates = []

    # Create 2 successful candidates
    for i in range(2):
        job = Job(
            job_key=f"job_{i}",
            source_type="greenhouse",
            source_identifier="test",
            external_id=f"ext_{i}",
            title=f"Job {i}",
            company="Test Corp",
            location="Remote",
            description="Test",
            url=f"https://test.com/{i}",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash=f"hash_{i}",
        )

        match_result = MatchResult(
            is_match=True,
            matched_required_terms={"test"},
            snippets=["Test"],
            summary="Test",
        )

        norm_result = create_norm_result(job, is_new=True, content_changed=True)

        candidates.append(
            CandidateMatch(
                normalization_result=norm_result,
                match_result=match_result,
            )
        )

    mock_renderer = Mock()
    mock_renderer.render.return_value = {
        "subject": "Test",
        "html_body": "<html>Test</html>",
        "text_body": "Test",
    }

    mock_smtp = Mock()
    mock_smtp.send.return_value = None

    mock_alert_repo = Mock()
    mock_alert_repo.has_been_sent.return_value = False

    service = NotificationService(
        template_renderer=mock_renderer,
        smtp_client=mock_smtp,
    )

    with caplog.at_level("INFO"):
        service.send_notifications(
            candidates, env_config, email_config, mock_alert_repo
        )

    # Should log summary
    assert "Notification batch complete" in caplog.text
    assert "2 sent" in caplog.text
