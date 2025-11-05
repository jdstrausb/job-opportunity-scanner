"""Notification service for sending email alerts about matching job postings.

This module provides the complete notification pipeline:
- NotificationService: Main service for sending job match alerts
- NotificationResult: Result data structure for notification outcomes
- TemplateRenderer: Jinja2-based email template rendering
- SMTPClient: SMTP wrapper with TLS/SSL support
- Payload utilities: Context builders for templates

The notification service integrates with the matching engine and persistence
layer to deliver templated email notifications with retry logic and deduplication.
"""

from .models import (
    NotificationError,
    NotificationResult,
    NotificationTemplateError,
    SMTPDeliveryError,
)
from .payloads import build_notification_context
from .service import NotificationService
from .smtp_client import (
    SMTPClient,
    build_sender_address,
    parse_recipients,
)
from .templates import TemplateRenderer

__all__ = [
    # Main service
    "NotificationService",
    # Models and results
    "NotificationResult",
    # Exceptions
    "NotificationError",
    "NotificationTemplateError",
    "SMTPDeliveryError",
    # Components
    "TemplateRenderer",
    "SMTPClient",
    # Utilities
    "build_notification_context",
    "build_sender_address",
    "parse_recipients",
]
