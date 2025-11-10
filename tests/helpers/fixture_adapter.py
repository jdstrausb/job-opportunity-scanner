"""Fixture-based adapter for testing.

This module provides a mock adapter that loads job data from YAML fixtures
instead of making real HTTP requests to ATS APIs. Used for deterministic
integration testing.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.adapters.base import BaseAdapter
from app.config.models import SourceConfig
from app.domain.models import RawJob


def load_fixture_jobs(fixture_path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Load job fixtures from YAML file.

    Args:
        fixture_path: Path to YAML file containing job fixtures

    Returns:
        Dictionary mapping source identifiers to lists of job data dicts

    Raises:
        FileNotFoundError: If fixture file doesn't exist
        yaml.YAMLError: If YAML parsing fails
    """
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture file not found: {fixture_path}")

    with open(fixture_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return data.get("sources", {})


class FixtureAdapter(BaseAdapter):
    """Mock adapter that returns jobs from YAML fixtures.

    This adapter mimics the interface of real ATS adapters but loads
    job data from a YAML file instead of making HTTP requests. Used
    for deterministic integration testing.

    Attributes:
        fixture_data: Dictionary mapping source identifiers to job lists
    """

    def __init__(self, fixture_path: Path, **kwargs):
        """Initialize fixture adapter.

        Args:
            fixture_path: Path to YAML file containing job fixtures
            **kwargs: Additional arguments passed to BaseAdapter
        """
        super().__init__(**kwargs)
        self.fixture_data = load_fixture_jobs(fixture_path)

    def fetch_jobs(self, source_config: SourceConfig) -> List[RawJob]:
        """Fetch jobs from fixture data for the given source.

        Args:
            source_config: Source configuration with identifier

        Returns:
            List of RawJob instances loaded from fixtures

        Raises:
            KeyError: If source identifier not found in fixtures
        """
        source_id = source_config.identifier

        if source_id not in self.fixture_data:
            # Return empty list if source not in fixtures (mimics no jobs found)
            return []

        jobs_data = self.fixture_data[source_id]
        raw_jobs = []

        for job_dict in jobs_data:
            # Parse timestamps if present
            posted_at = self._parse_fixture_timestamp(job_dict.get("posted_at"))
            updated_at = self._parse_fixture_timestamp(job_dict.get("updated_at"))

            raw_job = RawJob(
                external_id=str(job_dict["external_id"]),
                title=job_dict["title"],
                company=job_dict.get("company", source_config.name),
                location=job_dict.get("location", ""),
                description=job_dict.get("description", ""),
                url=job_dict.get("url", f"https://example.com/jobs/{job_dict['external_id']}"),
                posted_at=posted_at,
                updated_at=updated_at,
            )
            raw_jobs.append(raw_job)

        # Apply max_jobs truncation if configured
        return self._truncate_jobs(raw_jobs, "FixtureAdapter", source_id)

    def _parse_fixture_timestamp(self, timestamp_value: Optional[Any]) -> Optional[datetime]:
        """Parse timestamp from fixture data.

        Supports:
        - ISO 8601 strings (delegates to BaseAdapter._parse_timestamp)
        - datetime objects (returned as-is with UTC timezone)
        - None (returns None)

        Args:
            timestamp_value: Timestamp value from fixture

        Returns:
            UTC-aware datetime or None
        """
        if timestamp_value is None:
            return None

        if isinstance(timestamp_value, datetime):
            # Ensure UTC timezone
            if timestamp_value.tzinfo is None:
                return timestamp_value.replace(tzinfo=timezone.utc)
            return timestamp_value.astimezone(timezone.utc)

        if isinstance(timestamp_value, str):
            # Use base adapter's ISO 8601 parser
            return self._parse_timestamp(timestamp_value)

        # Unknown type, log warning and return None
        return None
