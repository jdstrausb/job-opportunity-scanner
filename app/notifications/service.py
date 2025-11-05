"""Notification service for sending job match alerts.

This module provides the main NotificationService class that orchestrates
the notification pipeline: deduplication checking, template rendering,
email delivery with retry/backoff, and alert persistence coordination.
"""

import logging
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Iterable, List, Optional

from app.config.environment import EnvironmentConfig
from app.config.models import EmailConfig
from app.logging import get_logger
from app.logging.context import log_context
from app.matching.models import CandidateMatch
from app.persistence.repositories import AlertRepository

from .models import (
    NotificationResult,
    NotificationTemplateError,
    SMTPDeliveryError,
)
from .payloads import build_notification_context
from .smtp_client import SMTPClient, build_sender_address, parse_recipients
from .templates import TemplateRenderer

logger = get_logger(__name__, component="notification")


class NotificationService:
    """Service for sending email notifications about matched jobs.

    Coordinates the entire notification flow:
    1. Check if notification needed (should_notify, content_changed)
    2. Check for duplicates via AlertRepository
    3. Build template context
    4. Render email templates
    5. Deliver via SMTP with retry/backoff
    6. Record alert on success (caller commits transaction)

    The service operates within the caller's database transaction and
    does not commit changes itself.
    """

    def __init__(
        self,
        template_renderer: Optional[TemplateRenderer] = None,
        smtp_client: Optional[SMTPClient] = None,
        logger_instance: Optional[logging.Logger] = None,
    ):
        """Initialize notification service.

        Args:
            template_renderer: Template renderer instance (creates default if None)
            smtp_client: SMTP client instance (creates default if None)
            logger_instance: Logger instance (uses module logger if None)
        """
        self.template_renderer = template_renderer or TemplateRenderer()
        self.smtp_client = smtp_client or SMTPClient()
        self.logger = logger_instance or logger

    def send_candidate_match(
        self,
        candidate: CandidateMatch,
        env_config: EnvironmentConfig,
        email_config: EmailConfig,
        alert_repo: AlertRepository,
    ) -> NotificationResult:
        """Send notification for a candidate match.

        Performs all necessary checks, renders templates, delivers email with
        retry logic, and records alert on success. The caller is responsible
        for committing the database transaction.

        Args:
            candidate: CandidateMatch to notify about
            env_config: Environment configuration with SMTP settings
            email_config: Email configuration with retry settings
            alert_repo: Alert repository for deduplication (tied to caller's session)

        Returns:
            NotificationResult indicating outcome and whether to persist alert
        """
        job = candidate.job
        job_key = job.job_key
        version_hash = job.content_hash
        notification_id = f"{job_key}:{version_hash[:8]}"

        # Set notification context for all logs
        with log_context(job_key=job_key, notification_id=notification_id):
            # Step 1: Verify notification should be sent
            if not candidate.should_notify:
                self.logger.info(
                    f"Skipping notification for job {job_key} - should_notify=False",
                    extra={"event": "notification.skip", "reason": "should_notify_false"}
                )
                return NotificationResult(
                    job_key=job_key,
                    version_hash=version_hash,
                    attempts=0,
                    status="skipped",
                )

            if not candidate.content_changed:
                self.logger.info(
                    f"Skipping notification for job {job_key} - content unchanged",
                    extra={"event": "notification.skip", "reason": "content_unchanged"}
                )
                return NotificationResult(
                    job_key=job_key,
                    version_hash=version_hash,
                    attempts=0,
                    status="skipped",
                )

            # Step 2: Check for duplicate
            if alert_repo.has_been_sent(job_key, version_hash):
                self.logger.info(
                    f"Skipping notification for job {job_key} - already sent",
                    extra={"event": "notification.duplicate"}
                )
                return NotificationResult(
                    job_key=job_key,
                    version_hash=version_hash,
                    attempts=0,
                    status="duplicate",
                )

            # Step 3: Build template context
            try:
                context = build_notification_context(candidate)
            except Exception as e:
                error_msg = f"Failed to build notification context: {e}"
                self.logger.error(error_msg, exc_info=True)
                return NotificationResult(
                    job_key=job_key,
                    version_hash=version_hash,
                    attempts=0,
                    status="failed",
                    error=error_msg,
                )

            # Step 4: Render templates
            try:
                rendered = self.template_renderer.render(context)
                subject = rendered["subject"]
                html_body = rendered["html_body"]
                text_body = rendered["text_body"]
            except NotificationTemplateError as e:
                # Template errors are fatal (developer misconfiguration)
                error_msg = f"Template rendering failed: {e}"
                self.logger.error(error_msg, exc_info=True)
                return NotificationResult(
                    job_key=job_key,
                    version_hash=version_hash,
                    attempts=0,
                    status="failed",
                    error=error_msg,
                )

            # Step 5: Build email message
            try:
                recipients = parse_recipients(env_config.alert_to_email)
                sender = build_sender_address(env_config)

                message = EmailMessage()
                message["Subject"] = subject
                message["From"] = sender
                message["To"] = ", ".join(recipients)

                # Set plain text body
                message.set_content(text_body)

                # Add HTML alternative
                message.add_alternative(html_body, subtype="html")

            except ValueError as e:
                error_msg = f"Failed to build email message: {e}"
                self.logger.error(error_msg)
                return NotificationResult(
                    job_key=job_key,
                    version_hash=version_hash,
                    attempts=0,
                    status="failed",
                    error=error_msg,
                )

            # Step 6: Send with retry/backoff
            max_attempts = email_config.max_retries + 1
            attempt = 0
            last_error = None

            for attempt in range(1, max_attempts + 1):
                # Apply backoff delay for retries (not on first attempt)
                if attempt > 1:
                    delay = email_config.retry_initial_delay * (
                        email_config.retry_backoff_multiplier ** (attempt - 2)
                    )
                    # Clamp to reasonable maximum (60 seconds)
                    delay = min(delay, 60.0)
                    self.logger.warning(
                        f"Retrying delivery for job {job_key} (attempt {attempt}/{max_attempts}) after {delay:.1f}s delay",
                        extra={
                            "event": "notification.send.attempt",
                            "attempt": attempt,
                        }
                    )
                    time.sleep(delay)

                try:
                    self.smtp_client.send(message, env_config, email_config.use_tls)

                    # Success!
                    self.logger.info(
                        f"Notification sent successfully for job {job_key} "
                        f"({job.company} - {job.title}) to {', '.join(recipients)} "
                        f"(attempts: {attempt})",
                        extra={
                            "event": "notification.send.success",
                            "attempt": attempt,
                            "recipients": recipients,
                        }
                    )

                    # Record alert in repository (caller will commit)
                    now = datetime.now(timezone.utc)
                    alert_repo.record_alert(job_key, version_hash, now)

                    return NotificationResult(
                        job_key=job_key,
                        version_hash=version_hash,
                        attempts=attempt,
                        status="sent",
                        should_persist_alert=True,
                    )

                except SMTPDeliveryError as e:
                    last_error = str(e)
                    error_type = type(e).__name__
                    retry_remaining = attempt < max_attempts

                    if retry_remaining:
                        self.logger.warning(
                            f"SMTP delivery failed for job {job_key} (attempt {attempt}/{max_attempts}): {e}",
                            extra={
                                "event": "notification.send.failure",
                                "attempt": attempt,
                                "error_type": error_type,
                                "retry_remaining": True,
                            }
                        )
                    else:
                        self.logger.error(
                            f"SMTP delivery failed for job {job_key} after {max_attempts} attempts: {e}",
                            exc_info=True,
                            extra={
                                "event": "notification.send.failure",
                                "job_key": job_key,
                                "company": job.company,
                                "title": job.title,
                                "attempts": max_attempts,
                                "attempt": attempt,
                                "error_type": error_type,
                                "retry_remaining": False,
                            },
                        )

            # All attempts exhausted
            return NotificationResult(
                job_key=job_key,
                version_hash=version_hash,
                attempts=max_attempts,
                status="failed",
                error=last_error,
            )

    def send_notifications(
        self,
        matches: Iterable[CandidateMatch],
        env_config: EnvironmentConfig,
        email_config: EmailConfig,
        alert_repo: AlertRepository,
    ) -> List[NotificationResult]:
        """Send notifications for multiple matches.

        Convenience method for batch processing. Continues processing even
        if individual notifications fail.

        Args:
            matches: Iterable of CandidateMatch objects to process
            env_config: Environment configuration
            email_config: Email configuration
            alert_repo: Alert repository

        Returns:
            List of NotificationResult objects (one per match)
        """
        results = []

        for candidate in matches:
            try:
                result = self.send_candidate_match(
                    candidate, env_config, email_config, alert_repo
                )
                results.append(result)
            except Exception as e:
                # Catch any unexpected errors to prevent batch failure
                self.logger.error(
                    f"Unexpected error processing notification for job {candidate.job.job_key}: {e}",
                    exc_info=True,
                )
                results.append(
                    NotificationResult(
                        job_key=candidate.job.job_key,
                        version_hash=candidate.job.content_hash,
                        attempts=0,
                        status="failed",
                        error=str(e),
                    )
                )

        # Log summary statistics
        sent = sum(1 for r in results if r.status == "sent")
        skipped = sum(1 for r in results if r.status == "skipped")
        duplicates = sum(1 for r in results if r.status == "duplicate")
        failed = sum(1 for r in results if r.status == "failed")

        self.logger.info(
            f"Notification batch complete: {sent} sent, {skipped} skipped, "
            f"{duplicates} duplicates, {failed} failed (total: {len(results)})"
        )

        return results
