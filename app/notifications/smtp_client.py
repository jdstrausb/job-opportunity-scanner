"""SMTP client wrapper for email delivery.

This module provides a thin wrapper around Python's smtplib with support
for TLS/SSL, authentication, and proper connection lifecycle management.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Callable, List, Optional

from email_validator import EmailNotValidError, validate_email

from app.config.environment import EnvironmentConfig

from .models import SMTPDeliveryError

logger = logging.getLogger(__name__)


class SMTPClient:
    """Wrapper around smtplib for sending email messages.

    Handles connection lifecycle, TLS/SSL negotiation, authentication,
    and recipient validation. Designed to be easily mockable for testing.
    """

    def __init__(
        self,
        smtp_factory: Optional[Callable] = None,
        smtp_ssl_factory: Optional[Callable] = None,
    ):
        """Initialize SMTP client with optional factory injection.

        Args:
            smtp_factory: Factory function for creating SMTP instances (for mocking)
            smtp_ssl_factory: Factory function for creating SMTP_SSL instances (for mocking)
        """
        self.smtp_factory = smtp_factory or smtplib.SMTP
        self.smtp_ssl_factory = smtp_ssl_factory or smtplib.SMTP_SSL

    def send(
        self,
        message: EmailMessage,
        env_config: EnvironmentConfig,
        use_tls: bool = True,
    ) -> None:
        """Send an email message via SMTP.

        Handles connection, TLS/SSL upgrade, authentication, and ensures
        proper cleanup on both success and failure.

        Args:
            message: Fully constructed EmailMessage to send
            env_config: Environment configuration with SMTP settings
            use_tls: Whether to use TLS (STARTTLS or implicit SSL)

        Raises:
            SMTPDeliveryError: If message delivery fails
        """
        smtp = None
        try:
            # Determine connection type based on port
            if env_config.smtp_port == 465:
                # Port 465: Implicit TLS (SMTP_SSL)
                logger.debug(
                    f"Connecting to {env_config.smtp_host}:{env_config.smtp_port} with implicit TLS"
                )
                context = ssl.create_default_context()
                smtp = self.smtp_ssl_factory(
                    env_config.smtp_host, env_config.smtp_port, context=context
                )
            else:
                # Standard SMTP with optional STARTTLS
                logger.debug(
                    f"Connecting to {env_config.smtp_host}:{env_config.smtp_port}"
                )
                smtp = self.smtp_factory(env_config.smtp_host, env_config.smtp_port)

                # Upgrade to TLS if requested and not using implicit TLS
                if use_tls:
                    logger.debug("Upgrading connection with STARTTLS")
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)

            # Authenticate if credentials provided
            if env_config.smtp_user and env_config.smtp_pass:
                logger.debug(f"Authenticating as {env_config.smtp_user}")
                smtp.login(env_config.smtp_user, env_config.smtp_pass)
            else:
                logger.debug("No authentication credentials provided, proceeding without auth")

            # Send the message
            smtp.send_message(message)
            logger.debug(f"Message sent successfully to {message['To']}")

        except smtplib.SMTPException as e:
            error_msg = f"SMTP error during message delivery: {e}"
            logger.error(error_msg)
            raise SMTPDeliveryError(error_msg) from e
        except OSError as e:
            error_msg = f"Network error during SMTP connection: {e}"
            logger.error(error_msg)
            raise SMTPDeliveryError(error_msg) from e
        except Exception as e:
            error_msg = f"Unexpected error during SMTP delivery: {e}"
            logger.error(error_msg)
            raise SMTPDeliveryError(error_msg) from e
        finally:
            # Always close connection
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception as e:
                    logger.warning(f"Error closing SMTP connection: {e}")


def parse_recipients(recipient_string: str) -> List[str]:
    """Parse and validate comma-separated email addresses.

    Args:
        recipient_string: Comma-separated email addresses

    Returns:
        List of validated email addresses

    Raises:
        ValueError: If any email address is invalid
    """
    recipients = []
    raw_emails = [email.strip() for email in recipient_string.split(",")]

    for email in raw_emails:
        if not email:
            continue

        try:
            # Use email-validator for robust validation
            validated = validate_email(email, check_deliverability=False)
            recipients.append(validated.normalized)
        except EmailNotValidError as e:
            raise ValueError(
                f"Invalid email address in ALERT_TO_EMAIL: '{email}' - {e}"
            ) from e

    if not recipients:
        raise ValueError("No valid email addresses found in ALERT_TO_EMAIL")

    return recipients


def build_sender_address(env_config: EnvironmentConfig) -> str:
    """Build the 'From' address for outgoing emails.

    Uses SMTP_SENDER_NAME with SMTP_USER if available, otherwise falls back
    to a noreply address at the SMTP host.

    Args:
        env_config: Environment configuration with SMTP settings

    Returns:
        Formatted sender address (e.g., "Job Scanner <user@example.com>")
    """
    sender_name = env_config.smtp_sender_name

    if env_config.smtp_user:
        # Use actual SMTP user as the email address
        sender_email = env_config.smtp_user
    else:
        # Fall back to noreply@hostname
        sender_email = f"noreply@{env_config.smtp_host}"

    return f"{sender_name} <{sender_email}>"
