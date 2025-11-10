"""Base adapter class with shared functionality for all ATS adapters.

This module provides the abstract base class that all ATS adapters must implement,
along with shared utilities for HTTP requests, HTML cleaning, and timestamp parsing.
"""

import html
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from app.config.models import SourceConfig
from app.domain.models import RawJob
from app.logging import get_logger

from .exceptions import (
    AdapterConfigurationError,
    AdapterError,
    AdapterHTTPError,
    AdapterResponseError,
    AdapterTimeoutError,
)

logger = get_logger(__name__, component="adapter")


class BaseAdapter(ABC):
    """Base class for all ATS adapters.

    Provides shared HTTP request handling, error management, and utilities
    for HTML cleaning and timestamp parsing.

    All ATS adapters must inherit from this class and implement the
    fetch_jobs() abstract method.

    Attributes:
        timeout: HTTP request timeout in seconds
        user_agent: User-Agent header for HTTP requests
        max_jobs: Maximum jobs to return per source (0 = unlimited)
    """

    def __init__(self, timeout: int = 30, user_agent: str = "JobOpportunityScanner/1.0", max_jobs: int = 1000) -> None:
        """Initialize adapter with configuration.

        Args:
            timeout: HTTP request timeout in seconds (default 30, range 5-300)
            user_agent: User-Agent header for requests (default "JobOpportunityScanner/1.0")
            max_jobs: Maximum jobs to return per source (default 1000, 0 = unlimited)

        Raises:
            AdapterConfigurationError: If timeout is outside valid range or user_agent is empty
        """
        if not 5 <= timeout <= 300:
            raise AdapterConfigurationError(
                f"Timeout must be between 5 and 300 seconds, got: {timeout}"
            )
        if not user_agent or not user_agent.strip():
            raise AdapterConfigurationError("user_agent cannot be empty")

        self.timeout = timeout
        self.user_agent = user_agent.strip()
        self.max_jobs = max_jobs

        # Create session with user agent
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self.user_agent})

    @abstractmethod
    def fetch_jobs(self, source_config: SourceConfig) -> list[RawJob]:
        """Fetch jobs from ATS API.

        This method must be implemented by all subclasses. It should:
        1. Make HTTP request(s) to the ATS API endpoint
        2. Parse the response
        3. Transform ATS-specific data to RawJob domain models
        4. Handle errors gracefully (raise AdapterError or return empty list)

        Args:
            source_config: Source configuration with ATS type and identifier

        Returns:
            List of RawJob domain models. Empty list if no jobs found or on transient errors.

        Raises:
            AdapterError: On fatal errors (fatal HTTP errors, malformed responses, etc.)
            Its subclasses indicate specific error types:
            - AdapterHTTPError: HTTP 4xx/5xx errors
            - AdapterResponseError: Response parsing/validation failed
            - AdapterTimeoutError: Request timed out
        """
        pass

    def _make_request(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request with error handling.

        Handles:
        - Setting user agent and timeout
        - Connection errors and timeouts
        - HTTP error status codes
        - Invalid JSON responses
        - Logging of request details

        Args:
            url: URL to request
            method: HTTP method (default "GET")
            headers: Additional headers to include (merged with defaults)
            params: Query parameters
            json_data: JSON body for POST requests

        Returns:
            Parsed JSON response as dictionary or list

        Raises:
            AdapterHTTPError: On 4xx or 5xx HTTP status
            AdapterTimeoutError: On request timeout
            AdapterResponseError: On invalid JSON or other response parsing errors
        """
        # Prepare headers
        request_headers = self._session.headers.copy()
        if headers:
            request_headers.update(headers)

        try:
            logger.debug(
                f"HTTP {method} request to {url}",
                extra={
                    "event": "adapter.fetch.request",
                    "method": method,
                    "url": url,
                    "timeout": self.timeout,
                },
            )

            response = self._session.request(
                method=method,
                url=url,
                headers=request_headers,
                params=params,
                json=json_data,
                timeout=self.timeout,
            )

            # Check for HTTP errors
            if response.status_code >= 400:
                # Determine if this is a retryable error (5xx) or fatal (4xx)
                is_retryable = response.status_code >= 500
                event_name = "adapter.fetch.retryable_error" if is_retryable else "adapter.fetch.error"
                log_level = logging.WARNING if is_retryable else logging.ERROR

                logger.log(
                    log_level,
                    f"HTTP {response.status_code} error from {url}",
                    extra={
                        "event": event_name,
                        "status_code": response.status_code,
                        "url": url,
                        "retry_after_seconds": None,
                    }
                )

                raise AdapterHTTPError(
                    f"HTTP {response.status_code}: {response.reason}",
                    status_code=response.status_code,
                    url=url,
                )

            # Parse JSON response
            try:
                data = response.json()
                logger.debug(
                    "HTTP request succeeded",
                    extra={
                        "event": "adapter.fetch.succeeded",
                        "status_code": response.status_code,
                        "url": url,
                    }
                )
                return data
            except (ValueError, requests.exceptions.JSONDecodeError) as e:
                logger.error(
                    f"Failed to parse JSON response from {url}",
                    extra={
                        "event": "adapter.fetch.error",
                        "error_type": "JSONDecodeError",
                        "url": url,
                    }
                )
                raise AdapterResponseError(
                    f"Failed to parse JSON response from {url}: {e}"
                ) from e

        except requests.exceptions.Timeout as e:
            logger.warning(
                f"Request to {url} timed out after {self.timeout} seconds",
                extra={
                    "event": "adapter.fetch.retryable_error",
                    "error_type": "Timeout",
                    "url": url,
                    "timeout": self.timeout,
                }
            )
            raise AdapterTimeoutError(
                f"Request to {url} timed out after {self.timeout} seconds",
                url=url,
            ) from e
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Request to {url} failed: {e}",
                extra={
                    "event": "adapter.fetch.error",
                    "error_type": type(e).__name__,
                    "url": url,
                }
            )
            raise AdapterHTTPError(
                f"Request to {url} failed: {e}",
                status_code=0,
                url=url,
            ) from e

    def _clean_html(self, html_text: str) -> str:
        """Clean HTML tags and entities from text.

        Performs the following transformations:
        1. Decode HTML entities (&amp; → &, etc.)
        2. Convert <br> and </p> to newlines
        3. Strip remaining HTML tags
        4. Collapse multiple whitespace to single space (preserve newlines)
        5. Collapse multiple newlines to double newline (paragraph separation)
        6. Strip leading/trailing whitespace

        Args:
            html_text: Text containing HTML formatting

        Returns:
            Plain text with whitespace normalized and HTML removed
        """
        if not html_text:
            return ""

        text = html_text

        # Decode HTML entities (&amp; → &, &nbsp; → space, etc.)
        text = html.unescape(text)

        # Convert <br> and </p> to newlines for better readability
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)

        # Strip all remaining HTML tags, replacing them with a space
        text = re.sub(r"<[^>]+>", " ", text)

        # Collapse horizontal whitespace to single space (preserve newlines)
        text = re.sub(r"[ \t]+", " ", text)

        # Collapse multiple newlines to double newline (paragraph separation)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Strip leading/trailing whitespace from entire text and each line
        text = text.strip()

        return text

    def _parse_timestamp(self, timestamp_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO 8601 timestamp string to UTC datetime.

        Handles:
        - ISO 8601 format with 'Z' suffix (e.g., "2025-11-04T10:30:00Z")
        - ISO 8601 format with milliseconds (e.g., "2025-11-04T10:30:00.123Z")
        - ISO 8601 format with timezone offset (e.g., "2025-11-04T10:30:00+00:00")
        - None values (returns None)

        Always returns UTC-aware datetime. If parsing fails, logs warning and returns None.

        Args:
            timestamp_str: ISO 8601 timestamp string, or None

        Returns:
            UTC-aware datetime object, or None if timestamp_str is None or parsing fails
        """
        if not timestamp_str:
            return None

        try:
            # Replace 'Z' suffix with UTC offset for fromisoformat compatibility
            # Python 3.11+ fromisoformat handles most ISO 8601 formats
            normalized = timestamp_str
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"

            dt = datetime.fromisoformat(normalized)

            # Ensure we have a UTC-aware datetime
            if dt.tzinfo is None:
                # Assume UTC if no timezone info
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                # Convert to UTC if different timezone
                dt = dt.astimezone(timezone.utc)

            return dt

        except (ValueError, AttributeError) as e:
            logger.warning(
                "Failed to parse timestamp",
                extra={"timestamp": timestamp_str, "error": str(e)},
            )
            return None

    def _truncate_jobs(self, jobs: list[RawJob], adapter_name: str, source_identifier: str) -> list[RawJob]:
        """Truncate job list to max_jobs limit if configured.

        Args:
            jobs: List of RawJob objects
            adapter_name: Name of the adapter (for logging)
            source_identifier: Company identifier (for logging)

        Returns:
            Original list if max_jobs is 0 or unset, otherwise truncated to max_jobs
        """
        if self.max_jobs > 0 and len(jobs) > self.max_jobs:
            logger.warning(
                "Truncating jobs to max_jobs limit",
                extra={
                    "adapter": adapter_name,
                    "source": source_identifier,
                    "total": len(jobs),
                    "max": self.max_jobs,
                },
            )
            return jobs[: self.max_jobs]

        return jobs
