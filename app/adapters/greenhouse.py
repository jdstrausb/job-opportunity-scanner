"""Greenhouse ATS adapter implementation."""

from __future__ import annotations

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
        params = {"content": "true"}  # Request the full job description content

        logger.info(
            "Fetching jobs from Greenhouse",
            extra={
                "adapter": self.ADAPTER_NAME,
                "source": source_config.identifier,
                "url": url,
            },
        )

        try:
            response = self._make_request(url, params=params)

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

    def _extract_metadata_text(self, metadata: list[dict]) -> str:
        """Extracts and formats relevant text from the metadata array."""
        if not metadata:
            return ""

        extracted_texts = []
        # Target specific metadata fields that contain useful keywords
        fields_of_interest = {
            "Career Site Department": "Department",
            "Department": "Department",
            "Cost Center": "Cost Center",
            "Employment Type": "Employment Type",
        }

        for item in metadata:
            name = item.get("name")
            value = item.get("value")

            if name in fields_of_interest and value:
                label = fields_of_interest[name]
                # The value can be a string or a list of strings
                if isinstance(value, list):
                    value_str = ", ".join(filter(None, value))
                else:
                    value_str = str(value)

                if value_str:
                    extracted_texts.append(f"{label}: {value_str}")

        return "\n".join(extracted_texts)

    def _get_combined_location(self, job: dict) -> str | None:
        """Intelligently combines top-level and metadata locations."""
        top_level_location = job.get("location", {}).get("name") if job.get("location") else None
        
        metadata_location = None
        if job.get("metadata"):
            for item in job["metadata"]:
                if item.get("name") == "Job Posting Location" and item.get("value"):
                    value = item["value"]
                    if isinstance(value, list):
                        metadata_location = ", ".join(filter(None, value))
                    else:
                        metadata_location = str(value)
                    break

        if top_level_location and metadata_location and top_level_location.lower() != metadata_location.lower():
            return f"{top_level_location} ({metadata_location})"
        
        return top_level_location or metadata_location

    def _get_enriched_description(self, job: dict, source_config: SourceConfig) -> str:
        """Combines the main description with relevant metadata fields."""
        description_html = job.get("content") or job.get("description") or ""
        metadata_text = self._extract_metadata_text(job.get("metadata", []))
        
        full_description = f"{description_html}\n\n{metadata_text}".strip()

        if not full_description:
            logger.warning(
                "Job description is empty after checking 'content', 'description', and metadata.",
                extra={
                    "adapter": self.ADAPTER_NAME,
                    "source": source_config.identifier,
                    "job_id": job.get("id"),
                    "job_title": job.get("title"),
                },
            )
        return full_description

    def _transform_job(self, job: dict, source_config: SourceConfig) -> RawJob:
        """Transform Greenhouse job object to RawJob domain model."""
        final_location = self._get_combined_location(job)
        full_description = self._get_enriched_description(job, source_config)

        # Create RawJob with transformed fields
        return RawJob(
            external_id=str(job["id"]),
            title=job["title"],
            company=source_config.name,
            location=final_location,
            description=self._clean_html(full_description),
            url=job["absolute_url"],
            posted_at=self._parse_timestamp(job.get("first_published")),
            updated_at=self._parse_timestamp(job.get("updated_at")),
        )
