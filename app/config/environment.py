"""Environment variable loading and validation."""

import os
import re
from typing import Optional, Tuple

from pydantic import EmailStr, ValidationError

from .exceptions import ConfigurationError


class EnvironmentConfig:
    """Environment variable configuration holder."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: Optional[str],
        smtp_pass: Optional[str],
        alert_to_email: str,
        smtp_sender_name: Optional[str] = None,
        log_level: Optional[str] = None,
        database_url: Optional[str] = None,
    ):
        """Initialize environment configuration."""
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_pass = smtp_pass
        self.alert_to_email = alert_to_email
        self.smtp_sender_name = smtp_sender_name or "Job Opportunity Scanner"
        self.log_level = log_level
        self.database_url = database_url or "sqlite:///./data/job_scanner.db"


def load_environment_config() -> EnvironmentConfig:
    """
    Load and validate environment variables.

    Required environment variables:
    - SMTP_HOST: SMTP server hostname
    - SMTP_PORT: SMTP server port (1-65535)
    - ALERT_TO_EMAIL: Email address to send alerts to

    Optional environment variables:
    - SMTP_USER: SMTP authentication username (if auth required)
    - SMTP_PASS: SMTP authentication password (if auth required)
    - SMTP_SENDER_NAME: Display name for email sender
    - LOG_LEVEL: Override log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - DATABASE_URL: SQLite database URL (default: sqlite:///./data/job_scanner.db)

    Returns:
        EnvironmentConfig object with validated values

    Raises:
        ConfigurationError: If required variables are missing or invalid
    """
    errors = []

    # Load required variables
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port_str = os.getenv("SMTP_PORT")
    alert_to_email = os.getenv("ALERT_TO_EMAIL")

    # Load optional variables
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_sender_name = os.getenv("SMTP_SENDER_NAME")
    log_level = os.getenv("LOG_LEVEL")
    database_url = os.getenv("DATABASE_URL")

    # Validate required variables
    if not smtp_host:
        errors.append("Missing required environment variable: SMTP_HOST")

    if not smtp_port_str:
        errors.append("Missing required environment variable: SMTP_PORT")

    if not alert_to_email:
        errors.append("Missing required environment variable: ALERT_TO_EMAIL")

    # Validate SMTP_PORT is numeric and in valid range
    smtp_port = None
    if smtp_port_str:
        try:
            smtp_port = int(smtp_port_str)
            if smtp_port < 1 or smtp_port > 65535:
                errors.append(
                    f"Invalid SMTP_PORT: {smtp_port}. Must be between 1 and 65535."
                )
        except ValueError:
            errors.append(
                f"Invalid SMTP_PORT: '{smtp_port_str}'. Must be a valid integer."
            )

    # Validate email address format
    if alert_to_email:
        # Support multiple email addresses separated by commas
        email_addresses = [email.strip() for email in alert_to_email.split(",")]
        for email in email_addresses:
            if not _is_valid_email(email):
                errors.append(
                    f"Invalid email address format in ALERT_TO_EMAIL: '{email}'"
                )

    # Validate log level if provided
    if log_level:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if log_level.upper() not in valid_levels:
            errors.append(
                f"Invalid LOG_LEVEL: '{log_level}'. Must be one of: {', '.join(valid_levels)}"
            )

    # Validate SMTP authentication consistency
    if smtp_user and not smtp_pass:
        errors.append(
            "SMTP_USER is set but SMTP_PASS is not. Both must be set for authentication."
        )
    elif smtp_pass and not smtp_user:
        errors.append(
            "SMTP_PASS is set but SMTP_USER is not. Both must be set for authentication."
        )

    # If there are validation errors, raise exception
    if errors:
        raise ConfigurationError(
            "Environment variable validation failed",
            errors=errors,
            suggestions=[
                "Copy .env.example to .env and fill in your credentials",
                "Ensure all required environment variables are set",
                "Check that email addresses are valid",
                "Verify SMTP_PORT is a number between 1 and 65535",
            ],
        )

    return EnvironmentConfig(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_pass=smtp_pass,
        alert_to_email=alert_to_email,
        smtp_sender_name=smtp_sender_name,
        log_level=log_level,
        database_url=database_url,
    )


def _is_valid_email(email: str) -> bool:
    """
    Validate email address format.

    Args:
        email: Email address to validate

    Returns:
        True if valid, False otherwise
    """
    # Simple regex-based validation
    # More comprehensive validation would use email-validator library
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))
