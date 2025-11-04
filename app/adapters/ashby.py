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

    Ashby provides a GraphQL API for accessing job postings.
    This adapter queries the GraphQL endpoint and transforms results to RawJob
    domain models.

    API Details:
        Endpoint: https://jobs.ashby.com/api/graphql
        Method: POST (GraphQL)
        Authentication: None required for public job boards (some orgs may require API key)
        Response: GraphQL response with data or errors field
    """

    ADAPTER_NAME = "ashby"
    API_ENDPOINT = "https://jobs.ashby.com/api/graphql"

    # GraphQL query for fetching job postings
    GRAPHQL_QUERY = """
    query JobBoard($organizationIdentifier: String!) {
      jobBoard(organizationIdentifier: $organizationIdentifier) {
        jobPostings {
          id
          title
          location {
            name
          }
          description
          externalLink
          publishedDate
          updatedAt
        }
      }
    }
    """

    def fetch_jobs(self, source_config: SourceConfig) -> list[RawJob]:
        """Fetch jobs from Ashby GraphQL API.

        Queries the Ashby GraphQL endpoint for job postings for the configured
        organization and transforms them to RawJob domain models.

        Args:
            source_config: Source configuration with identifier (organization ID)

        Returns:
            List of RawJob objects, empty list on transient errors

        Raises:
            AdapterError: On fatal errors (invalid config, response parsing, GraphQL errors)
        """
        payload = {
            "query": self.GRAPHQL_QUERY,
            "variables": {
                "organizationIdentifier": source_config.identifier,
            },
        }

        logger.info(
            "Fetching jobs from Ashby",
            extra={
                "adapter": self.ADAPTER_NAME,
                "source": source_config.identifier,
                "url": self.API_ENDPOINT,
            },
        )

        try:
            response = self._make_request(self.API_ENDPOINT, method="POST", json_data=payload)

            # Check for GraphQL errors
            if "errors" in response and response["errors"]:
                errors = response["errors"]
                error_messages = [
                    err.get("message", "Unknown error") if isinstance(err, dict) else str(err)
                    for err in errors
                ]
                raise AdapterResponseError(f"GraphQL errors: {', '.join(error_messages)}")

            # Extract job board from response
            data = response.get("data")
            if not data:
                logger.warning(
                    "No data in GraphQL response",
                    extra={
                        "adapter": self.ADAPTER_NAME,
                        "source": source_config.identifier,
                    },
                )
                return []

            job_board = data.get("jobBoard")
            if not job_board:
                logger.warning(
                    "No job board found for organization",
                    extra={
                        "adapter": self.ADAPTER_NAME,
                        "source": source_config.identifier,
                    },
                )
                return []

            jobs_data = job_board.get("jobPostings", [])
            if not isinstance(jobs_data, list):
                raise AdapterResponseError(
                    f"Expected 'jobPostings' to be array, got {type(jobs_data).__name__}"
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
            location.name → location (may be None)
            description → description (HTML cleaned)
            externalLink → url
            publishedDate → posted_at (ISO 8601 parsed to UTC)
            updatedAt → updated_at (ISO 8601 parsed to UTC)

        Args:
            job: Job posting object from Ashby GraphQL response
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
            external_id=job["id"],
            title=job["title"],
            company=source_config.name,
            location=location,
            description=self._clean_html(job["description"]),  # Clean HTML
            url=job["externalLink"],
            posted_at=self._parse_timestamp(job.get("publishedDate")),
            updated_at=self._parse_timestamp(job.get("updatedAt")),
        )
