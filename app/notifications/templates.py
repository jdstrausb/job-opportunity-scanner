"""Template rendering for email notifications using Jinja2.

This module wraps Jinja2 template rendering with caching and strict
undefined checking to catch template errors early.
"""

import logging
from typing import Dict, Optional

from jinja2 import Environment, PackageLoader, StrictUndefined, TemplateError

from .models import NotificationTemplateError

logger = logging.getLogger(__name__)


class TemplateRenderer:
    """Renders email templates using Jinja2.

    Provides methods to render subject lines and body content (HTML and plain text)
    from template files in the app.notifications.email_templates package.

    Templates are cached for reuse across multiple invocations.
    """

    def __init__(
        self,
        template_dir: str = "email_templates",
        subject_template: str = "job_alert_subject.j2",
        html_template: str = "job_alert_body.html.j2",
        text_template: str = "job_alert_body.txt.j2",
    ):
        """Initialize template renderer with Jinja2 environment.

        Args:
            template_dir: Directory name within app.notifications package
            subject_template: Filename of subject line template
            html_template: Filename of HTML body template
            text_template: Filename of plain text body template
        """
        self.subject_template_name = subject_template
        self.html_template_name = html_template
        self.text_template_name = text_template

        # Initialize Jinja2 environment
        self.env = Environment(
            loader=PackageLoader("app.notifications", template_dir),
            autoescape=True,  # Auto-escape HTML for safety
            undefined=StrictUndefined,  # Raise errors for missing variables
        )

        logger.debug(f"Initialized TemplateRenderer with templates from {template_dir}")

    def render(self, context: Dict) -> Dict[str, str]:
        """Render all email templates with the provided context.

        Args:
            context: Dictionary of template variables

        Returns:
            Dictionary containing:
            - subject: Rendered subject line (single line, no newlines)
            - html_body: Rendered HTML body
            - text_body: Rendered plain text body

        Raises:
            NotificationTemplateError: If template rendering fails
        """
        try:
            # Load templates (cached after first load)
            subject_template = self.env.get_template(self.subject_template_name)
            html_template = self.env.get_template(self.html_template_name)
            text_template = self.env.get_template(self.text_template_name)

            # Render subject (strip whitespace and ensure single line)
            subject = subject_template.render(context).strip().replace("\n", " ")

            # Render bodies
            html_body = html_template.render(context)
            text_body = text_template.render(context)

            logger.debug(f"Rendered templates for job: {context.get('job_key', 'unknown')}")

            return {
                "subject": subject,
                "html_body": html_body,
                "text_body": text_body,
            }

        except TemplateError as e:
            error_msg = f"Template rendering failed: {e}"
            logger.error(error_msg, exc_info=True)
            raise NotificationTemplateError(error_msg) from e
        except Exception as e:
            error_msg = f"Unexpected error during template rendering: {e}"
            logger.error(error_msg, exc_info=True)
            raise NotificationTemplateError(error_msg) from e
