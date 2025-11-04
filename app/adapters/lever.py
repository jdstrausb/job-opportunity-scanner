"""Lever ATS adapter implementation."""

import logging
from datetime import datetime, timezone

from app.config.models import SourceConfig
from app.domain.models import RawJob

from .base import BaseAdapter
from .exceptions import AdapterError, AdapterHTTPError, AdapterResponseError

logger = logging.getLogger(__name__)


class LeverAdapter(BaseAdapter):
    """Adapter for Lever ATS.

    Lever provides a public job board API that returns job postings
    as a JSON array. This adapter fetches jobs and transforms them to RawJob
    domain models.

    API Details:
        Endpoint: https://api.lever.co/v0/postings/{identifier}?mode=json
        Method: GET
        Authentication: None (public)
        Response: JSON array of posting objects (not wrapped in object)
    """

    ADAPTER_NAME = "lever"
    API_BASE_URL = "https://api.lever.co/v0/postings"

    def fetch_jobs(self, source_config: SourceConfig) -> list[RawJob]:
        """Fetch jobs from Lever API.

        Fetches all postings for the configured company and transforms them
        to RawJob domain models. Lever returns both HTML and plain text descriptions;
        we prefer plain text.

        Args:
            source_config: Source configuration with identifier (company handle)

        Returns:
            List of RawJob objects, empty list on transient errors

        Raises:
            AdapterError: On fatal errors (invalid config, response parsing)
        """
        url = f"{self.API_BASE_URL}/{source_config.identifier}"
        params = {"mode": "json"}

        logger.info(
            "Fetching jobs from Lever",
            extra={
                "adapter": self.ADAPTER_NAME,
                "source": source_config.identifier,
                "url": url,
            },
        )

        try:
            response = self._make_request(url, params=params)

            # Lever returns array directly, not wrapped in object
            # Handle both array and object responses for robustness
            if isinstance(response, list):
                jobs_data = response
            elif isinstance(response, dict):
                # Fallback if API changes
                jobs_data = response.get("postings", [])
            else:
                raise AdapterResponseError(
                    f"Expected JSON array or object, got {type(response).__name__}"
                )

            # Truncate to max_jobs if configured
            jobs_data = self._truncate_jobs(jobs_data, self.ADAPTER_NAME, source_config.identifier)

            raw_jobs = []
            for job in jobs_data:
                try:
                    raw_job = self._transform_job(job, source_config)
                    raw_jobs.append(raw_job)
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(
                        "Failed to transform Lever job",
                        extra={
                            "adapter": self.ADAPTER_NAME,
                            "source": source_config.identifier,
                            "job_id": job.get("id"),
                            "error": str(e),
                        },
                    )
                    # Continue with other jobs

            logger.info(
                "Successfully fetched jobs from Lever",
                extra={
                    "adapter": self.ADAPTER_NAME,
                    "source": source_config.identifier,
                    "count": len(raw_jobs),
                },
            )

            return raw_jobs

        except AdapterHTTPError as e:
            # 404 means company doesn't exist - log and return empty
            if e.status_code == 404:
                logger.warning(
                    "Lever company not found",
                    extra={
                        "adapter": self.ADAPTER_NAME,
                        "source": source_config.identifier,
                        "url": url,
                    },
                )
                return []

            # 5xx errors are transient - log and return empty for retry on next run
            if e.status_code >= 500:
                logger.warning(
                    "Lever API error (transient)",
                    extra={
                        "adapter": self.ADAPTER_NAME,
                        "source": source_config.identifier,
                        "status": e.status_code,
                    },
                )
                return []

            # Other HTTP errors are fatal
            raise

        except (AdapterResponseError, AdapterError):
            # Fatal errors - propagate up
            raise

    def _transform_job(self, job: dict, source_config: SourceConfig) -> RawJob:
        """Transform Lever posting object to RawJob domain model.

        Field mapping:
            id → external_id
            text → title
            source_config.name → company
            categories.location → location (may be None)
            descriptionPlain + additionalPlain → description (plain text preferred)
            hostedUrl → url
            createdAt → posted_at (Unix milliseconds converted to UTC)
            updatedAt → updated_at (Unix milliseconds converted to UTC)

        Args:
            job: Posting object from Lever API response
            source_config: Source configuration with company name

        Returns:
            RawJob domain model

        Raises:
            KeyError: If required field is missing
            ValueError: If field transformation fails
        """
        # Extract location from nested structure
        location = None
        if "categories" in job and job["categories"] and isinstance(job["categories"], dict):
            location = job["categories"].get("location")

        # Build description from plain text fields (preferred) or fall back to HTML
        description = self._get_description(job)

        # Parse timestamps (Lever uses Unix timestamps in milliseconds)
        posted_at = self._parse_unix_timestamp_ms(job.get("createdAt"))
        updated_at = self._parse_unix_timestamp_ms(job.get("updatedAt"))

        # Create RawJob with transformed fields
        return RawJob(
            external_id=job["id"],  # Already a string UUID
            title=job["text"],
            company=source_config.name,
            location=location,
            description=description,
            url=job["hostedUrl"],
            posted_at=posted_at,
            updated_at=updated_at,
        )

    def _get_description(self, job: dict) -> str:
        """Extract and combine description from Lever job object.

        Lever provides both plain text and HTML descriptions. Prefer plain text
        to avoid HTML cleaning issues. Combines descriptionPlain and additionalPlain.

        Args:
            job: Job object from Lever API

        Returns:
            Combined description text (plain text preferred, HTML as fallback)
        """
        # Prefer plain text
        description_plain = job.get("descriptionPlain", "").strip() if job.get("descriptionPlain") else ""
        additional_plain = job.get("additionalPlain", "").strip() if job.get("additionalPlain") else ""

        if description_plain or additional_plain:
            # Combine both parts with newline separation
            parts = [p for p in [description_plain, additional_plain] if p]
            return "\n\n".join(parts)

        # Fall back to HTML if plain text not available
        description_html = job.get("description", "").strip() if job.get("description") else ""
        additional_html = job.get("additional", "").strip() if job.get("additional") else ""

        if description_html or additional_html:
            parts = [p for p in [description_html, additional_html] if p]
            combined_html = "\n\n".join(parts)
            return self._clean_html(combined_html)

        return ""

    @staticmethod
    def _parse_unix_timestamp_ms(timestamp_ms: int | None) -> datetime | None:
        """Parse Unix timestamp in milliseconds to UTC datetime.

        Lever API uses Unix timestamps in milliseconds (not seconds).
        This converts to UTC-aware datetime.

        Args:
            timestamp_ms: Unix timestamp in milliseconds, or None

        Returns:
            UTC-aware datetime, or None if input is None
        """
        if not timestamp_ms:
            return None

        try:
            # Convert milliseconds to seconds
            timestamp_s = timestamp_ms / 1000
            return datetime.fromtimestamp(timestamp_s, tz=timezone.utc)
        except (TypeError, ValueError, OSError) as e:
            logger.warning(
                "Failed to parse Unix timestamp",
                extra={"timestamp_ms": timestamp_ms, "error": str(e)},
            )
            return None
