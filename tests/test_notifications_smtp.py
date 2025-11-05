"""Unit tests for SMTP client wrapper.

Tests the SMTPClient for:
- Connection handling (SMTP and SMTP_SSL)
- TLS/STARTTLS negotiation
- Authentication (with and without credentials)
- Message delivery
- Error handling and exceptions
- Recipient parsing and validation
- Sender address building
"""

import smtplib
from email.message import EmailMessage
from unittest.mock import MagicMock, Mock, patch, call

import pytest
from email_validator import EmailNotValidError

from app.config.environment import EnvironmentConfig
from app.notifications.models import SMTPDeliveryError
from app.notifications.smtp_client import (
    SMTPClient,
    build_sender_address,
    parse_recipients,
)


@pytest.fixture
def env_config_with_auth():
    """Environment config with SMTP authentication."""
    return EnvironmentConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user@example.com",
        smtp_pass="secret123",
        alert_to_email="recipient@example.com",
        smtp_sender_name="Job Scanner",
    )


@pytest.fixture
def env_config_without_auth():
    """Environment config without SMTP authentication."""
    return EnvironmentConfig(
        smtp_host="smtp.example.com",
        smtp_port=25,
        smtp_user=None,
        smtp_pass=None,
        alert_to_email="recipient@example.com",
        smtp_sender_name="Job Scanner",
    )


@pytest.fixture
def env_config_implicit_tls():
    """Environment config for implicit TLS (port 465)."""
    return EnvironmentConfig(
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_user="user@gmail.com",
        smtp_pass="apppassword",
        alert_to_email="recipient@example.com",
        smtp_sender_name="Job Scanner",
    )


@pytest.fixture
def sample_message():
    """Sample email message for testing."""
    msg = EmailMessage()
    msg["Subject"] = "Test Subject"
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg.set_content("Test body")
    return msg


def test_smtp_client_initialization():
    """Test SMTPClient initialization."""
    client = SMTPClient()
    assert client is not None
    assert client.smtp_factory is not None
    assert client.smtp_ssl_factory is not None


def test_smtp_client_send_with_starttls(env_config_with_auth, sample_message):
    """Test sending email with STARTTLS (port 587)."""
    mock_smtp = MagicMock()
    mock_factory = Mock(return_value=mock_smtp)

    client = SMTPClient(smtp_factory=mock_factory)
    client.send(sample_message, env_config_with_auth, use_tls=True)

    # Should create SMTP connection
    mock_factory.assert_called_once_with("smtp.example.com", 587)

    # Should call starttls
    mock_smtp.starttls.assert_called_once()

    # Should authenticate
    mock_smtp.login.assert_called_once_with("user@example.com", "secret123")

    # Should send message
    mock_smtp.send_message.assert_called_once_with(sample_message)

    # Should quit
    mock_smtp.quit.assert_called_once()


def test_smtp_client_send_with_implicit_tls(env_config_implicit_tls, sample_message):
    """Test sending email with implicit TLS (port 465)."""
    mock_smtp_ssl = MagicMock()
    mock_ssl_factory = Mock(return_value=mock_smtp_ssl)

    client = SMTPClient(smtp_ssl_factory=mock_ssl_factory)
    client.send(sample_message, env_config_implicit_tls, use_tls=True)

    # Should create SMTP_SSL connection (not regular SMTP)
    mock_ssl_factory.assert_called_once()
    call_args = mock_ssl_factory.call_args
    assert call_args[0] == ("smtp.gmail.com", 465)
    assert "context" in call_args[1]  # SSL context passed

    # Should NOT call starttls (already using SSL)
    mock_smtp_ssl.starttls.assert_not_called()

    # Should authenticate
    mock_smtp_ssl.login.assert_called_once_with("user@gmail.com", "apppassword")

    # Should send message
    mock_smtp_ssl.send_message.assert_called_once_with(sample_message)

    # Should quit
    mock_smtp_ssl.quit.assert_called_once()


def test_smtp_client_send_without_auth(env_config_without_auth, sample_message):
    """Test sending email without authentication."""
    mock_smtp = MagicMock()
    mock_factory = Mock(return_value=mock_smtp)

    client = SMTPClient(smtp_factory=mock_factory)
    client.send(sample_message, env_config_without_auth, use_tls=False)

    # Should create SMTP connection
    mock_factory.assert_called_once_with("smtp.example.com", 25)

    # Should NOT call login (no credentials)
    mock_smtp.login.assert_not_called()

    # Should still send message
    mock_smtp.send_message.assert_called_once_with(sample_message)

    # Should quit
    mock_smtp.quit.assert_called_once()


def test_smtp_client_send_without_tls(env_config_with_auth, sample_message):
    """Test sending email without TLS."""
    mock_smtp = MagicMock()
    mock_factory = Mock(return_value=mock_smtp)

    client = SMTPClient(smtp_factory=mock_factory)
    client.send(sample_message, env_config_with_auth, use_tls=False)

    # Should NOT call starttls
    mock_smtp.starttls.assert_not_called()

    # Should still authenticate and send
    mock_smtp.login.assert_called_once()
    mock_smtp.send_message.assert_called_once()


def test_smtp_client_handles_smtp_exception(env_config_with_auth, sample_message):
    """Test that SMTP exceptions are wrapped in SMTPDeliveryError."""
    mock_smtp = MagicMock()
    mock_smtp.send_message.side_effect = smtplib.SMTPException("Connection failed")
    mock_factory = Mock(return_value=mock_smtp)

    client = SMTPClient(smtp_factory=mock_factory)

    with pytest.raises(SMTPDeliveryError) as exc_info:
        client.send(sample_message, env_config_with_auth, use_tls=True)

    assert "SMTP error" in str(exc_info.value)
    # Should still attempt to quit
    mock_smtp.quit.assert_called_once()


def test_smtp_client_handles_network_error(env_config_with_auth, sample_message):
    """Test that network errors are wrapped in SMTPDeliveryError."""
    mock_smtp = MagicMock()
    mock_smtp.starttls.side_effect = OSError("Network unreachable")
    mock_factory = Mock(return_value=mock_smtp)

    client = SMTPClient(smtp_factory=mock_factory)

    with pytest.raises(SMTPDeliveryError) as exc_info:
        client.send(sample_message, env_config_with_auth, use_tls=True)

    assert "Network error" in str(exc_info.value)


def test_smtp_client_closes_connection_on_error(env_config_with_auth, sample_message):
    """Test that connection is closed even on error."""
    mock_smtp = MagicMock()
    mock_smtp.send_message.side_effect = smtplib.SMTPException("Send failed")
    mock_factory = Mock(return_value=mock_smtp)

    client = SMTPClient(smtp_factory=mock_factory)

    try:
        client.send(sample_message, env_config_with_auth, use_tls=True)
    except SMTPDeliveryError:
        pass

    # Should still call quit in finally block
    mock_smtp.quit.assert_called_once()


def test_parse_recipients_single_email():
    """Test parsing single email address."""
    recipients = parse_recipients("user@example.com")

    assert len(recipients) == 1
    assert recipients[0] == "user@example.com"


def test_parse_recipients_multiple_emails():
    """Test parsing comma-separated email addresses."""
    recipients = parse_recipients("user1@example.com, user2@example.com, user3@test.org")

    assert len(recipients) == 3
    assert "user1@example.com" in recipients
    assert "user2@example.com" in recipients
    assert "user3@test.org" in recipients


def test_parse_recipients_with_whitespace():
    """Test parsing emails with extra whitespace."""
    recipients = parse_recipients("  user1@example.com  ,  user2@example.com  ")

    assert len(recipients) == 2
    # Should be trimmed
    assert recipients[0] == "user1@example.com"
    assert recipients[1] == "user2@example.com"


def test_parse_recipients_invalid_email():
    """Test that invalid email raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        parse_recipients("not-an-email")

    assert "Invalid email address" in str(exc_info.value)
    assert "not-an-email" in str(exc_info.value)


def test_parse_recipients_mixed_valid_invalid():
    """Test that one invalid email fails the entire parse."""
    with pytest.raises(ValueError) as exc_info:
        parse_recipients("valid@example.com, invalid-email, another@test.com")

    assert "Invalid email address" in str(exc_info.value)


def test_parse_recipients_empty_string():
    """Test that empty string raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        parse_recipients("")

    assert "No valid email addresses found" in str(exc_info.value)


def test_parse_recipients_only_whitespace():
    """Test that whitespace-only string raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        parse_recipients("   ,  ,  ")

    assert "No valid email addresses found" in str(exc_info.value)


def test_build_sender_address_with_smtp_user():
    """Test building sender address when SMTP_USER is set."""
    env_config = EnvironmentConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="notifications@example.com",
        smtp_pass="secret",
        alert_to_email="user@example.com",
        smtp_sender_name="Job Opportunity Scanner",
    )

    sender = build_sender_address(env_config)

    assert sender == "Job Opportunity Scanner <notifications@example.com>"


def test_build_sender_address_without_smtp_user():
    """Test building sender address when SMTP_USER is not set."""
    env_config = EnvironmentConfig(
        smtp_host="mail.example.com",
        smtp_port=25,
        smtp_user=None,
        smtp_pass=None,
        alert_to_email="user@example.com",
        smtp_sender_name="Job Scanner",
    )

    sender = build_sender_address(env_config)

    # Should use noreply@hostname
    assert sender == "Job Scanner <noreply@mail.example.com>"


def test_build_sender_address_uses_custom_sender_name():
    """Test that custom sender name is used."""
    env_config = EnvironmentConfig(
        smtp_host="smtp.test.com",
        smtp_port=587,
        smtp_user="bot@test.com",
        smtp_pass="pass",
        alert_to_email="user@test.com",
        smtp_sender_name="Custom Alert Bot",
    )

    sender = build_sender_address(env_config)

    assert sender.startswith("Custom Alert Bot")
    assert "bot@test.com" in sender
