"""Data models and exceptions for the notification service.

This module defines result types and custom exceptions used throughout
the notification pipeline.
"""

from dataclasses import dataclass
from typing import Optional


class NotificationError(Exception):
    """Base exception for notification-related errors."""

    pass


class NotificationTemplateError(NotificationError):
    """Raised when template rendering fails due to configuration or missing variables."""

    pass


class SMTPDeliveryError(NotificationError):
    """Raised when SMTP delivery fails after all retry attempts."""

    pass


@dataclass
class NotificationResult:
    """Result of attempting to send a notification for a job match.

    Captures the outcome, attempt count, and any errors encountered during
    the notification process. Used by the pipeline to decide on persistence
    and metrics collection.

    Attributes:
        job_key: Unique identifier for the job
        version_hash: Content hash of the job version
        attempts: Number of send attempts made
        status: Outcome status (sent, skipped, duplicate, failed)
        error: Optional error message if delivery failed
        should_persist_alert: Whether caller should record alert in database
    """

    job_key: str
    version_hash: str
    attempts: int
    status: str  # "sent", "skipped", "duplicate", "failed"
    error: Optional[str] = None
    should_persist_alert: bool = False

    def is_success(self) -> bool:
        """Check if notification was successfully sent.

        Returns:
            True if status is "sent", False otherwise
        """
        return self.status == "sent"

    def should_record_alert(self) -> bool:
        """Check if alert should be recorded in the database.

        Returns:
            True if status is "sent" (only record successful sends)
        """
        return self.status == "sent"
