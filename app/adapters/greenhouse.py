"""Greenhouse ATS adapter implementation."""

import logging

from app.config.models import SourceConfig
from app.domain.models import RawJob

from .base import BaseAdapter
from .exceptions import AdapterError, AdapterHTTPError, AdapterResponseError

logger = logging.getLogger(__name__)


class GreenhouseAdapter(BaseAdapter):
    """Adapter for Greenhouse ATS.

    Greenhouse provides a public job board API that returns job postings
    as a JSON array. This adapter fetches jobs and transforms them to RawJob
    domain models.

    API Details:
        Endpoint: https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs
        Method: GET
        Authentication: None (public)
        Response: JSON object with 'jobs' array
    """

    ADAPTER_NAME = "greenhouse"
    API_BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

    def fetch_jobs(self, source_config: SourceConfig) -> list[RawJob]:
        """Fetch jobs from Greenhouse API.

        Fetches all jobs for the configured company board and transforms them
        to RawJob domain models. Greenhouse does not provide posted_at timestamps.

        Args:
            source_config: Source configuration with identifier (board token)

        Returns:
            List of RawJob objects, empty list on transient errors

        Raises:
            AdapterError: On fatal errors (invalid config, response parsing)
        """
        url = f"{self.API_BASE_URL}/{source_config.identifier}/jobs"

        logger.info(
            "Fetching jobs from Greenhouse",
            extra={
                "adapter": self.ADAPTER_NAME,
                "source": source_config.identifier,
                "url": url,
            },
        )

        try:
            response = self._make_request(url)

            # Extract jobs array from response
            if not isinstance(response, dict):
                raise AdapterResponseError(
                    f"Expected JSON object response, got {type(response).__name__}"
                )

            jobs_data = response.get("jobs", [])
            if not isinstance(jobs_data, list):
                raise AdapterResponseError(
                    f"Expected 'jobs' field to be array, got {type(jobs_data).__name__}"
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
                        "Failed to transform Greenhouse job",
                        extra={
                            "adapter": self.ADAPTER_NAME,
                            "source": source_config.identifier,
                            "job_id": job.get("id"),
                            "error": str(e),
                        },
                    )
                    # Continue with other jobs

            logger.info(
                "Successfully fetched jobs from Greenhouse",
                extra={
                    "adapter": self.ADAPTER_NAME,
                    "source": source_config.identifier,
                    "count": len(raw_jobs),
                },
            )

            return raw_jobs

        except AdapterHTTPError as e:
            # 404 means company board doesn't exist - log and return empty
            if e.status_code == 404:
                logger.warning(
                    "Greenhouse board not found",
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
                    "Greenhouse API error (transient)",
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
        """Transform Greenhouse job object to RawJob domain model.

        Field mapping:
            id → external_id (converted to string)
            title → title
            source_config.name → company
            location.name → location (may be None)
            content → description (HTML cleaned)
            absolute_url → url
            None → posted_at (not provided by Greenhouse)
            updated_at → updated_at (ISO 8601 parsed to UTC)

        Args:
            job: Job object from Greenhouse API response
            source_config: Source configuration with company name

        Returns:
            RawJob domain model

        Raises:
            KeyError: If required field is missing
            ValueError: If field transformation fails
        """
        # Extract location from nested structure
        location = None
        if "location" in job and job["location"] and isinstance(job["location"], dict):
            location = job["location"].get("name")

        # Create RawJob with transformed fields
        return RawJob(
            external_id=str(job["id"]),  # Convert int to string
            title=job["title"],
            company=source_config.name,
            location=location,
            description=self._clean_html(job["content"]),  # Clean HTML
            url=job["absolute_url"],
            posted_at=None,  # Greenhouse doesn't provide posted_at
            updated_at=self._parse_timestamp(job.get("updated_at")),
        )
