"""Ashby ATS adapter implementation."""

import logging
from typing import Any

from app.config.models import SourceConfig
from app.domain.models import RawJob

from .base import BaseAdapter
from .exceptions import AdapterError, AdapterHTTPError, AdapterResponseError

logger = logging.getLogger(__name__)


class AshbyAdapter(BaseAdapter):
    """Adapter for Ashby ATS.

    Ashby provides a public API for accessing job postings from a company's job board.
    This adapter queries the job board endpoint and transforms results to RawJob
    domain models.

    API Details:
        Endpoint: https://api.ashbyhq.com/posting-api/job-board/{organization_name}
        Method: GET
        Authentication: None required for public job boards
    """

    ADAPTER_NAME = "ashby"
    API_ENDPOINT = "https://api.ashbyhq.com/posting-api/job-board/"

    def fetch_jobs(self, source_config: SourceConfig) -> list[RawJob]:
        """Fetch jobs from Ashby API.

        Queries the Ashby job board endpoint for job postings for the configured
        organization and transforms them to RawJob domain models.

        Args:
            source_config: Source configuration with identifier (organization name)

        Returns:
            List of RawJob objects, empty list on transient errors

        Raises:
            AdapterError: On fatal errors (invalid config, response parsing)
        """
        url = f"{self.API_ENDPOINT}{source_config.identifier}"

        logger.info(
            "Fetching jobs from Ashby",
            extra={
                "adapter": self.ADAPTER_NAME,
                "source": source_config.identifier,
                "url": url,
            },
        )

        try:
            response = self._make_request(url, method="GET")

            if not isinstance(response, dict):
                raise AdapterResponseError(
                    f"Expected a dict response, got {type(response).__name__}"
                )

            jobs_data = response.get("jobs", [])
            if not isinstance(jobs_data, list):
                raise AdapterResponseError(
                    f"Expected 'jobs' to be a list, got {type(jobs_data).__name__}"
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
                        "Failed to transform Ashby job",
                        extra={
                            "adapter": self.ADAPTER_NAME,
                            "source": source_config.identifier,
                            "job_id": job.get("id"),
                            "error": str(e),
                        },
                    )
                    # Continue with other jobs

            logger.info(
                "Successfully fetched jobs from Ashby",
                extra={
                    "adapter": self.ADAPTER_NAME,
                    "source": source_config.identifier,
                    "count": len(raw_jobs),
                },
            )

            return raw_jobs

        except AdapterHTTPError as e:
            # 404 means organization doesn't exist - log and return empty
            if e.status_code == 404:
                logger.warning(
                    "Ashby organization not found",
                    extra={
                        "adapter": self.ADAPTER_NAME,
                        "source": source_config.identifier,
                    },
                )
                return []

            # 5xx errors are transient - log and return empty for retry on next run
            if e.status_code >= 500:
                logger.warning(
                    "Ashby API error (transient)",
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
        """Transform Ashby job posting to RawJob domain model.

        Field mapping:
            id → external_id
            title → title
            source_config.name → company
            location → location
            descriptionHtml → description (HTML cleaned)
            jobUrl → url
            publishedAt → posted_at (ISO 8601 parsed to UTC)
            updatedAt → updated_at (ISO 8601 parsed to UTC)
            secondaryLocations[].location → secondary_locations
            isRemote → is_remote
            address.postalAddress.addressCountry → location_country

        Args:
            job: Job posting object from Ashby API response
            source_config: Source configuration with company name

        Returns:
            RawJob domain model

        Raises:
            KeyError: If required field is missing
            ValueError: If field transformation fails
        """
        # Extract secondary locations
        secondary_locations = []
        secondary_locs_data = job.get("secondaryLocations", [])
        if isinstance(secondary_locs_data, list):
            for sec_loc in secondary_locs_data:
                if isinstance(sec_loc, dict) and "location" in sec_loc:
                    loc_str = sec_loc["location"]
                    if loc_str and isinstance(loc_str, str) and loc_str.strip():
                        secondary_locations.append(loc_str.strip())

        # Extract country from address if available
        location_country = None
        address = job.get("address")
        if address and isinstance(address, dict):
            postal_address = address.get("postalAddress")
            if postal_address and isinstance(postal_address, dict):
                country = postal_address.get("addressCountry")
                if country and isinstance(country, str) and country.strip():
                    location_country = country.strip()

        return RawJob(
            external_id=job["id"],
            title=job["title"],
            company=source_config.name,
            location=job.get("location"),
            description=self._clean_html(job.get("descriptionHtml", "")),
            url=job["jobUrl"],
            posted_at=self._parse_timestamp(job.get("publishedAt")),
            updated_at=self._parse_timestamp(job.get("updatedAt")),
            secondary_locations=secondary_locations,
            is_remote=job.get("isRemote"),
            location_country=location_country,
        )