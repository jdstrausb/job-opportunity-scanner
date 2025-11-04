# ATS Adapters Technical Specification (Step 5)

## Overview

This document provides the complete technical design for implementing ATS (Applicant Tracking System) adapters for the Job Opportunity Scanner. This corresponds to **Step 5** of the implementation guide in the main technical specification.

**Goal**: Implement Greenhouse, Lever, and Ashby adapters using a shared base class to fetch job postings from public ATS APIs and transform them into normalized `RawJob` domain models.

**Source Documents**:
- [docs/job-opportunity-scanner-prd.md](job-opportunity-scanner-prd.md)
- [docs/job-opportunity-scanner-techspec.md](job-opportunity-scanner-techspec.md)

**Audience**: Engineers implementing the adapter layer.

---

## Architecture Overview

### High-Level Flow

```
SourceConfig → Adapter.fetch_jobs() → List[RawJob] → Normalization Layer → Job
```

1. **Input**: `SourceConfig` object containing ATS type, company identifier, and metadata
2. **Processing**: Adapter makes HTTP request(s) to ATS API endpoint, parses response, handles errors
3. **Output**: List of `RawJob` domain models with validated fields and UTC timestamps

### Module Structure

```
app/adapters/
├── __init__.py              # Package exports
├── base.py                  # BaseAdapter abstract class + shared utilities
├── exceptions.py            # Adapter-specific exceptions
├── greenhouse.py            # Greenhouse adapter implementation
├── lever.py                 # Lever adapter implementation
├── ashby.py                 # Ashby adapter implementation
└── factory.py               # Factory function for adapter instantiation
```

### Integration Points

**Existing Components**:
- `app.config.models.SourceConfig`: Input configuration for each source
- `app.config.models.AdvancedConfig`: HTTP timeout and user-agent settings
- `app.domain.models.RawJob`: Output data structure
- `app.utils.timestamps`: UTC datetime utilities

**Future Components** (not yet implemented):
- Scheduler/Pipeline: Will call adapters via factory function
- Normalization Layer: Will convert `RawJob` → `Job` domain models

---

## Design Principles

1. **Isolation**: Each adapter is independent; failures do not cascade
2. **Consistency**: All adapters implement the same interface via abstract base class
3. **Resilience**: HTTP errors, timeouts, and malformed responses are handled gracefully
4. **Observability**: Log structured events for debugging (fetch start/end, error details, job counts)
5. **Testability**: Adapters accept dependencies via constructor; tests use recorded fixtures
6. **Standards**: UTC timestamps only, normalized text fields, strict type validation via Pydantic

---

## Base Adapter Design

### File: `app/adapters/base.py`

#### BaseAdapter Abstract Class

**Purpose**: Define the contract all ATS adapters must implement and provide shared HTTP request handling.

**Constructor Parameters**:
- `timeout: int` - HTTP request timeout in seconds (from `AdvancedConfig.http_request_timeout`, default 30)
- `user_agent: str` - User-Agent header for requests (from `AdvancedConfig.user_agent`, default "JobOpportunityScanner/1.0")
- `max_jobs: int` - Maximum jobs to return per source (from `AdvancedConfig.max_jobs_per_source`, default 1000, 0 = unlimited)

**Abstract Methods**:
- `fetch_jobs(source_config: SourceConfig) -> List[RawJob]`
  - Fetch jobs from ATS API for the given source
  - Return list of `RawJob` domain models
  - Raise `AdapterError` on fatal errors
  - Return empty list on transient errors (with logging)

**Shared Helper Methods**:
- `_make_request(url: str, method: str = "GET", headers: dict = None, params: dict = None, json_data: dict = None) -> dict`
  - Make HTTP request with configured timeout and user-agent
  - Handle connection errors, timeouts, HTTP errors
  - Parse JSON response
  - Raise `AdapterHTTPError` on HTTP errors (4xx, 5xx)
  - Raise `AdapterTimeoutError` on timeout
  - Raise `AdapterResponseError` on invalid JSON

- `_clean_html(html: str) -> str`
  - Strip HTML tags from description fields
  - Convert common HTML entities to text
  - Collapse multiple whitespace to single space
  - Return plain text string

- `_parse_timestamp(timestamp_str: Optional[str]) -> Optional[datetime]`
  - Parse ISO 8601 timestamp strings to UTC datetime
  - Handle None values gracefully
  - Return None if parsing fails (log warning)

**Logging**:
- Use Python's `logging` module with structured key-value format
- Include context: `adapter=<adapter_name>`, `source=<identifier>`, `event=<event_type>`
- Key events:
  - `fetch_start`: Beginning fetch operation
  - `fetch_success`: Completed successfully with job count
  - `fetch_error`: Failed with error details
  - `http_request`: HTTP request details (URL, method, status)

#### Example Base Implementation

```python
from abc import ABC, abstractmethod
import logging
from typing import List, Optional, Dict
from datetime import datetime
import requests
import re

from app.config.models import SourceConfig
from app.domain.models import RawJob
from .exceptions import AdapterError, AdapterHTTPError, AdapterTimeoutError, AdapterResponseError

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """Base class for all ATS adapters.

    Provides shared HTTP request handling, error management, and utilities
    for HTML cleaning and timestamp parsing.
    """

    def __init__(self, timeout: int = 30, user_agent: str = "JobOpportunityScanner/1.0", max_jobs: int = 1000):
        self.timeout = timeout
        self.user_agent = user_agent
        self.max_jobs = max_jobs
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})

    @abstractmethod
    def fetch_jobs(self, source_config: SourceConfig) -> List[RawJob]:
        """Fetch jobs from ATS API.

        Args:
            source_config: Source configuration with type and identifier

        Returns:
            List of RawJob domain models

        Raises:
            AdapterError: On unrecoverable errors
        """
        pass

    def _make_request(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        json_data: Optional[Dict] = None
    ) -> dict:
        """Make HTTP request with error handling."""
        # Implementation details
        pass

    def _clean_html(self, html: str) -> str:
        """Strip HTML tags and clean text."""
        # Implementation details
        pass

    def _parse_timestamp(self, timestamp_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO 8601 timestamp to UTC datetime."""
        # Implementation details
        pass
```

---

## Adapter-Specific Implementations

### 1. Greenhouse Adapter

**File**: `app/adapters/greenhouse.py`

#### API Details

- **Endpoint**: `https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs`
- **Method**: GET
- **Authentication**: None (public API)
- **Rate Limits**: Not publicly documented; use respectful defaults (timeout, user-agent)
- **Response Format**: JSON object with `jobs` array

#### Response Schema

```json
{
  "jobs": [
    {
      "id": 123456,
      "title": "Senior Software Engineer",
      "location": {
        "name": "San Francisco, CA"
      },
      "absolute_url": "https://boards.greenhouse.io/examplecorp/jobs/123456",
      "content": "<p>Job description with HTML formatting...</p>",
      "updated_at": "2025-11-04T10:30:00Z",
      "metadata": [
        {
          "id": 123,
          "name": "Employment Type",
          "value": "Full-time"
        }
      ]
    }
  ]
}
```

#### Field Mapping

| Greenhouse Field | RawJob Field | Transformation |
|-----------------|--------------|----------------|
| `id` | `external_id` | Convert to string |
| `title` | `title` | Direct mapping, strip whitespace |
| `source_config.name` | `company` | From configuration |
| `location.name` | `location` | Extract from nested object, may be None |
| `content` | `description` | Clean HTML tags |
| `absolute_url` | `url` | Direct mapping |
| N/A | `posted_at` | None (not provided by Greenhouse) |
| `updated_at` | `updated_at` | Parse ISO 8601 to UTC datetime |

#### Implementation Notes

1. **HTML Cleaning**: The `content` field contains HTML; use `_clean_html()` helper
2. **Location Handling**: Location may be missing or have empty `name` field; return None in these cases
3. **Posted Date**: Greenhouse does not provide `posted_at`; set to None
4. **ID Type**: Job `id` is an integer; convert to string for `external_id`
5. **Error Handling**:
   - 404 response: Log warning, return empty list (company board may not exist)
   - 5xx errors: Raise `AdapterHTTPError` for retry at pipeline level
   - Malformed JSON: Raise `AdapterResponseError`

#### Pagination

Greenhouse API returns all jobs in a single response (up to company's job count). No pagination required for typical use cases. If `max_jobs` is configured and response exceeds limit, truncate to first N jobs and log warning.

#### Example Implementation Pseudocode

```python
class GreenhouseAdapter(BaseAdapter):
    """Adapter for Greenhouse ATS."""

    def fetch_jobs(self, source_config: SourceConfig) -> List[RawJob]:
        """Fetch jobs from Greenhouse API."""
        url = f"https://boards-api.greenhouse.io/v1/boards/{source_config.identifier}/jobs"

        logger.info(
            "Fetching jobs from Greenhouse",
            extra={"adapter": "greenhouse", "source": source_config.identifier, "url": url}
        )

        try:
            response = self._make_request(url)
            jobs_data = response.get("jobs", [])

            if self.max_jobs > 0 and len(jobs_data) > self.max_jobs:
                logger.warning(
                    f"Truncating jobs to max_jobs limit",
                    extra={"adapter": "greenhouse", "source": source_config.identifier,
                           "total": len(jobs_data), "max": self.max_jobs}
                )
                jobs_data = jobs_data[:self.max_jobs]

            raw_jobs = []
            for job in jobs_data:
                raw_job = RawJob(
                    external_id=str(job["id"]),
                    title=job["title"],
                    company=source_config.name,
                    location=job.get("location", {}).get("name"),
                    description=self._clean_html(job["content"]),
                    url=job["absolute_url"],
                    posted_at=None,  # Not provided by Greenhouse
                    updated_at=self._parse_timestamp(job.get("updated_at"))
                )
                raw_jobs.append(raw_job)

            logger.info(
                "Successfully fetched jobs from Greenhouse",
                extra={"adapter": "greenhouse", "source": source_config.identifier,
                       "count": len(raw_jobs)}
            )

            return raw_jobs

        except AdapterError:
            raise  # Re-raise adapter errors
        except Exception as e:
            logger.error(
                f"Unexpected error fetching from Greenhouse: {e}",
                extra={"adapter": "greenhouse", "source": source_config.identifier},
                exc_info=True
            )
            raise AdapterError(f"Failed to fetch jobs from Greenhouse: {e}") from e
```

---

### 2. Lever Adapter

**File**: `app/adapters/lever.py`

#### API Details

- **Endpoint**: `https://api.lever.co/v0/postings/{identifier}?mode=json`
- **Method**: GET
- **Authentication**: None (public API)
- **Rate Limits**: Not publicly documented; use respectful defaults
- **Response Format**: JSON array of posting objects

#### Response Schema

```json
[
  {
    "id": "abc123-def456-ghi789",
    "text": "Senior Software Engineer",
    "categories": {
      "location": "San Francisco, CA",
      "commitment": "Full-time",
      "team": "Engineering"
    },
    "description": "<div>Job description with HTML...</div>",
    "descriptionPlain": "Job description as plain text...",
    "lists": [],
    "additional": "Additional HTML content",
    "additionalPlain": "Additional plain text",
    "hostedUrl": "https://jobs.lever.co/examplecorp/abc123-def456-ghi789",
    "applyUrl": "https://jobs.lever.co/examplecorp/abc123-def456-ghi789/apply",
    "createdAt": 1698854400000,
    "updatedAt": 1699459200000
  }
]
```

#### Field Mapping

| Lever Field | RawJob Field | Transformation |
|-------------|--------------|----------------|
| `id` | `external_id` | Direct mapping (UUID string) |
| `text` | `title` | Direct mapping, strip whitespace |
| `source_config.name` | `company` | From configuration |
| `categories.location` | `location` | Extract from nested object, may be None |
| `descriptionPlain` + `additionalPlain` | `description` | Combine plain text fields; fallback to cleaning HTML if plain text unavailable |
| `hostedUrl` | `url` | Direct mapping |
| `createdAt` | `posted_at` | Convert Unix timestamp (milliseconds) to UTC datetime |
| `updatedAt` | `updated_at` | Convert Unix timestamp (milliseconds) to UTC datetime |

#### Implementation Notes

1. **Timestamp Format**: Lever uses Unix timestamps in **milliseconds** (not seconds); divide by 1000 before converting to datetime
2. **Description Fields**: Lever provides both HTML (`description`) and plain text (`descriptionPlain`). Prefer plain text to avoid HTML cleaning issues. Combine `descriptionPlain` and `additionalPlain` fields if both exist.
3. **Location**: May be missing from `categories` object; return None if not present
4. **Response Format**: Response is a JSON array directly (not wrapped in an object)
5. **Error Handling**:
   - 404 response: Log warning, return empty list
   - Invalid company identifier: Returns empty array `[]`, not an error
   - Malformed JSON: Raise `AdapterResponseError`

#### Pagination

Lever API returns all postings in a single response. No pagination required.

#### Example Implementation Pseudocode

```python
class LeverAdapter(BaseAdapter):
    """Adapter for Lever ATS."""

    def fetch_jobs(self, source_config: SourceConfig) -> List[RawJob]:
        """Fetch jobs from Lever API."""
        url = f"https://api.lever.co/v0/postings/{source_config.identifier}"
        params = {"mode": "json"}

        logger.info(
            "Fetching jobs from Lever",
            extra={"adapter": "lever", "source": source_config.identifier, "url": url}
        )

        try:
            # Lever returns array directly, not wrapped in object
            response = self._make_request(url, params=params)

            # Handle case where response is array vs object
            if isinstance(response, list):
                jobs_data = response
            else:
                # Unexpected format, try to extract
                jobs_data = response.get("postings", [])

            if self.max_jobs > 0 and len(jobs_data) > self.max_jobs:
                logger.warning(
                    f"Truncating jobs to max_jobs limit",
                    extra={"adapter": "lever", "source": source_config.identifier,
                           "total": len(jobs_data), "max": self.max_jobs}
                )
                jobs_data = jobs_data[:self.max_jobs]

            raw_jobs = []
            for job in jobs_data:
                # Prefer plain text; fallback to cleaning HTML
                description_plain = job.get("descriptionPlain", "")
                additional_plain = job.get("additionalPlain", "")

                if description_plain or additional_plain:
                    description = f"{description_plain}\n\n{additional_plain}".strip()
                else:
                    # Fallback to HTML cleaning
                    description_html = job.get("description", "")
                    additional_html = job.get("additional", "")
                    combined_html = f"{description_html}\n\n{additional_html}".strip()
                    description = self._clean_html(combined_html)

                # Parse Unix timestamps (milliseconds)
                posted_at = None
                if "createdAt" in job and job["createdAt"]:
                    posted_at = datetime.fromtimestamp(job["createdAt"] / 1000, tz=timezone.utc)

                updated_at = None
                if "updatedAt" in job and job["updatedAt"]:
                    updated_at = datetime.fromtimestamp(job["updatedAt"] / 1000, tz=timezone.utc)

                raw_job = RawJob(
                    external_id=job["id"],
                    title=job["text"],
                    company=source_config.name,
                    location=job.get("categories", {}).get("location"),
                    description=description,
                    url=job["hostedUrl"],
                    posted_at=posted_at,
                    updated_at=updated_at
                )
                raw_jobs.append(raw_job)

            logger.info(
                "Successfully fetched jobs from Lever",
                extra={"adapter": "lever", "source": source_config.identifier,
                       "count": len(raw_jobs)}
            )

            return raw_jobs

        except AdapterError:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error fetching from Lever: {e}",
                extra={"adapter": "lever", "source": source_config.identifier},
                exc_info=True
            )
            raise AdapterError(f"Failed to fetch jobs from Lever: {e}") from e
```

---

### 3. Ashby Adapter

**File**: `app/adapters/ashby.py`

#### API Details

- **Endpoint**: `https://jobs.ashby.com/api/graphql`
- **Method**: POST (GraphQL)
- **Authentication**: May require API key for some organizations (check documentation)
- **Rate Limits**: Not publicly documented
- **Response Format**: GraphQL JSON response

#### GraphQL Query

```graphql
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
```

#### Response Schema

```json
{
  "data": {
    "jobBoard": {
      "jobPostings": [
        {
          "id": "ashby-job-id-123",
          "title": "Senior Software Engineer",
          "location": {
            "name": "Remote"
          },
          "description": "<p>Job description with HTML...</p>",
          "externalLink": "https://jobs.ashby.com/examplecorp/ashby-job-id-123",
          "publishedDate": "2025-11-01T12:00:00.000Z",
          "updatedAt": "2025-11-04T10:30:00.000Z"
        }
      ]
    }
  }
}
```

#### Field Mapping

| Ashby Field | RawJob Field | Transformation |
|-------------|--------------|----------------|
| `id` | `external_id` | Direct mapping |
| `title` | `title` | Direct mapping, strip whitespace |
| `source_config.name` | `company` | From configuration |
| `location.name` | `location` | Extract from nested object, may be None |
| `description` | `description` | Clean HTML tags |
| `externalLink` | `url` | Direct mapping |
| `publishedDate` | `posted_at` | Parse ISO 8601 to UTC datetime |
| `updatedAt` | `updated_at` | Parse ISO 8601 to UTC datetime |

#### Implementation Notes

1. **GraphQL Request**: Use POST method with JSON payload containing query and variables
2. **Error Handling**:
   - GraphQL errors: Check `errors` field in response; log and raise `AdapterResponseError`
   - 404 response: Log warning, return empty list
   - Missing `data.jobBoard`: Organization may not exist; return empty list
3. **HTML Cleaning**: Description field contains HTML; use `_clean_html()` helper
4. **Authentication**: Some organizations may require API key; document this in adapter but implement basic version first
5. **Timestamp Format**: Ashby uses ISO 8601 with milliseconds (e.g., `2025-11-01T12:00:00.000Z`)

#### Pagination

Ashby API may support pagination via GraphQL arguments (e.g., `first`, `after`). For v1.0, implement basic version without pagination. If response indicates pagination is needed (check `pageInfo` field), log warning and return available results.

#### Example Implementation Pseudocode

```python
class AshbyAdapter(BaseAdapter):
    """Adapter for Ashby ATS."""

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

    def fetch_jobs(self, source_config: SourceConfig) -> List[RawJob]:
        """Fetch jobs from Ashby GraphQL API."""
        url = "https://jobs.ashby.com/api/graphql"

        payload = {
            "query": self.GRAPHQL_QUERY,
            "variables": {
                "organizationIdentifier": source_config.identifier
            }
        }

        logger.info(
            "Fetching jobs from Ashby",
            extra={"adapter": "ashby", "source": source_config.identifier, "url": url}
        )

        try:
            response = self._make_request(url, method="POST", json_data=payload)

            # Check for GraphQL errors
            if "errors" in response:
                errors = response["errors"]
                error_messages = [err.get("message", "Unknown error") for err in errors]
                raise AdapterResponseError(f"GraphQL errors: {', '.join(error_messages)}")

            # Extract job postings from nested structure
            job_board = response.get("data", {}).get("jobBoard")
            if not job_board:
                logger.warning(
                    "No job board found for organization",
                    extra={"adapter": "ashby", "source": source_config.identifier}
                )
                return []

            jobs_data = job_board.get("jobPostings", [])

            if self.max_jobs > 0 and len(jobs_data) > self.max_jobs:
                logger.warning(
                    f"Truncating jobs to max_jobs limit",
                    extra={"adapter": "ashby", "source": source_config.identifier,
                           "total": len(jobs_data), "max": self.max_jobs}
                )
                jobs_data = jobs_data[:self.max_jobs]

            raw_jobs = []
            for job in jobs_data:
                raw_job = RawJob(
                    external_id=job["id"],
                    title=job["title"],
                    company=source_config.name,
                    location=job.get("location", {}).get("name") if job.get("location") else None,
                    description=self._clean_html(job["description"]),
                    url=job["externalLink"],
                    posted_at=self._parse_timestamp(job.get("publishedDate")),
                    updated_at=self._parse_timestamp(job.get("updatedAt"))
                )
                raw_jobs.append(raw_job)

            logger.info(
                "Successfully fetched jobs from Ashby",
                extra={"adapter": "ashby", "source": source_config.identifier,
                       "count": len(raw_jobs)}
            )

            return raw_jobs

        except AdapterError:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error fetching from Ashby: {e}",
                extra={"adapter": "ashby", "source": source_config.identifier},
                exc_info=True
            )
            raise AdapterError(f"Failed to fetch jobs from Ashby: {e}") from e
```

---

## Exception Hierarchy

**File**: `app/adapters/exceptions.py`

Define custom exceptions for adapter-specific errors to distinguish from other application errors and enable targeted error handling at the pipeline level.

```python
class AdapterError(Exception):
    """Base exception for all adapter errors."""
    pass


class AdapterHTTPError(AdapterError):
    """HTTP request failed (4xx, 5xx)."""

    def __init__(self, message: str, status_code: int, url: str):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class AdapterTimeoutError(AdapterError):
    """HTTP request timed out."""

    def __init__(self, message: str, url: str):
        super().__init__(message)
        self.url = url


class AdapterResponseError(AdapterError):
    """Response parsing or validation failed."""
    pass


class AdapterConfigurationError(AdapterError):
    """Invalid adapter configuration."""
    pass
```

**Error Handling Strategy**:
- **Fatal errors** (raise `AdapterError`): Invalid configuration, persistent 4xx errors
- **Transient errors** (log and return empty list): Timeouts, 5xx errors, network issues
- **Pipeline behavior**: Continue processing other sources even if one adapter fails

---

## Adapter Factory

**File**: `app/adapters/factory.py`

Provide a factory function to instantiate the correct adapter based on `SourceConfig.type`.

```python
from app.config.models import SourceConfig, AdvancedConfig
from .base import BaseAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .ashby import AshbyAdapter
from .exceptions import AdapterConfigurationError


def get_adapter(source_config: SourceConfig, advanced_config: AdvancedConfig) -> BaseAdapter:
    """Factory function to instantiate the appropriate ATS adapter.

    Args:
        source_config: Source configuration with ATS type and identifier
        advanced_config: Advanced configuration with timeout and user-agent settings

    Returns:
        Instantiated adapter for the specified ATS type

    Raises:
        AdapterConfigurationError: If ATS type is not supported

    Example:
        >>> source = SourceConfig(name="Example", type="greenhouse", identifier="example")
        >>> config = AdvancedConfig()
        >>> adapter = get_adapter(source, config)
        >>> jobs = adapter.fetch_jobs(source)
    """
    adapter_map = {
        "greenhouse": GreenhouseAdapter,
        "lever": LeverAdapter,
        "ashby": AshbyAdapter,
    }

    adapter_class = adapter_map.get(source_config.type.lower())
    if not adapter_class:
        raise AdapterConfigurationError(
            f"Unknown ATS type: {source_config.type}. Supported types: {', '.join(adapter_map.keys())}"
        )

    return adapter_class(
        timeout=advanced_config.http_request_timeout,
        user_agent=advanced_config.user_agent,
        max_jobs=advanced_config.max_jobs_per_source,
    )
```

**Usage Pattern**:
```python
# In scheduler/pipeline
from app.adapters.factory import get_adapter

for source in config.get_enabled_sources():
    adapter = get_adapter(source, config.advanced)
    try:
        raw_jobs = adapter.fetch_jobs(source)
        # Process raw_jobs...
    except AdapterError as e:
        logger.error(f"Failed to fetch from {source.name}: {e}")
        continue  # Process other sources
```

---

## Shared Utilities Implementation Details

### HTML Cleaning

**Requirements**:
- Strip all HTML tags (e.g., `<p>`, `<div>`, `<br>`)
- Convert HTML entities to text (e.g., `&amp;` → `&`, `&nbsp;` → space)
- Preserve line breaks (convert `<br>` and `</p>` to newlines)
- Collapse multiple whitespace to single space
- Strip leading/trailing whitespace

**Implementation Approach**:
```python
import re
import html


def _clean_html(html_text: str) -> str:
    """Clean HTML tags and entities from text."""
    # Decode HTML entities
    text = html.unescape(html_text)

    # Convert <br> and </p> to newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)

    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Collapse multiple whitespace to single space (preserve newlines)
    text = re.sub(r"[ \t]+", " ", text)

    # Collapse multiple newlines to double newline (paragraph separation)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Strip leading/trailing whitespace
    return text.strip()
```

**Testing**: Verify with fixtures containing HTML tables, lists, bold/italic text, and special characters.

### Timestamp Parsing

**Requirements**:
- Parse ISO 8601 timestamps (e.g., `2025-11-04T10:30:00Z`, `2025-11-04T10:30:00.123Z`)
- Parse Unix timestamps in milliseconds (Lever)
- Always return UTC-aware datetime
- Handle None values gracefully
- Log warnings on parse failures (return None instead of raising)

**Implementation Approach**:
```python
from datetime import datetime, timezone
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def _parse_timestamp(timestamp_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 timestamp to UTC datetime."""
    if not timestamp_str:
        return None

    try:
        # Use fromisoformat for Python 3.11+
        # Handles most ISO 8601 formats including 'Z' suffix
        if timestamp_str.endswith("Z"):
            timestamp_str = timestamp_str[:-1] + "+00:00"

        dt = datetime.fromisoformat(timestamp_str)

        # Ensure UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)

        return dt

    except (ValueError, AttributeError) as e:
        logger.warning(
            f"Failed to parse timestamp: {timestamp_str}",
            extra={"error": str(e)}
        )
        return None
```

---

## Testing Strategy

### Unit Tests

**File**: `tests/test_adapters.py`

#### Test Structure

1. **Base Adapter Tests**
   - Test abstract class cannot be instantiated
   - Test `_clean_html()` with various HTML inputs
   - Test `_parse_timestamp()` with valid/invalid inputs
   - Test `_make_request()` error handling (mock requests library)

2. **Greenhouse Adapter Tests**
   - Test successful fetch with recorded fixture
   - Test field mapping (ID conversion, location extraction, HTML cleaning)
   - Test missing location handling
   - Test 404 error handling (return empty list)
   - Test malformed JSON response
   - Test max_jobs truncation

3. **Lever Adapter Tests**
   - Test successful fetch with recorded fixture
   - Test Unix timestamp conversion (milliseconds)
   - Test plain text vs HTML fallback
   - Test combining description and additional fields
   - Test empty response (valid but no jobs)
   - Test max_jobs truncation

4. **Ashby Adapter Tests**
   - Test successful GraphQL fetch with recorded fixture
   - Test GraphQL error handling
   - Test missing jobBoard handling
   - Test location extraction from nested object
   - Test max_jobs truncation

5. **Factory Tests**
   - Test factory returns correct adapter class
   - Test factory passes configuration parameters
   - Test factory raises error for unknown ATS type

#### Test Fixtures

**Location**: `tests/fixtures/ats_responses/`

Create recorded JSON responses for each ATS:
- `greenhouse_sample_response.json`: Valid Greenhouse API response with 5 jobs
- `lever_sample_response.json`: Valid Lever API response with 5 jobs
- `ashby_sample_response.json`: Valid Ashby GraphQL response with 5 jobs
- `greenhouse_empty_response.json`: Empty jobs array
- `ashby_graphql_error.json`: GraphQL error response

**Example Test**:
```python
import json
import pytest
from unittest.mock import Mock, patch
from app.adapters.greenhouse import GreenhouseAdapter
from app.config.models import SourceConfig


@pytest.fixture
def greenhouse_response():
    """Load recorded Greenhouse API response."""
    with open("tests/fixtures/ats_responses/greenhouse_sample_response.json") as f:
        return json.load(f)


@pytest.fixture
def source_config():
    """Create test source configuration."""
    return SourceConfig(
        name="Example Corp",
        type="greenhouse",
        identifier="examplecorp"
    )


def test_greenhouse_fetch_jobs_success(greenhouse_response, source_config):
    """Test successful job fetch from Greenhouse."""
    adapter = GreenhouseAdapter(timeout=30)

    # Mock HTTP request
    with patch.object(adapter, "_make_request", return_value=greenhouse_response):
        raw_jobs = adapter.fetch_jobs(source_config)

    assert len(raw_jobs) == 5
    assert raw_jobs[0].external_id == "123456"
    assert raw_jobs[0].title == "Senior Software Engineer"
    assert raw_jobs[0].company == "Example Corp"
    assert raw_jobs[0].location == "San Francisco, CA"
    assert "<p>" not in raw_jobs[0].description  # HTML cleaned
    assert raw_jobs[0].url == "https://boards.greenhouse.io/examplecorp/jobs/123456"
    assert raw_jobs[0].posted_at is None  # Greenhouse doesn't provide
    assert raw_jobs[0].updated_at is not None


def test_greenhouse_fetch_jobs_truncates_to_max(greenhouse_response, source_config):
    """Test that adapter respects max_jobs setting."""
    adapter = GreenhouseAdapter(timeout=30, max_jobs=3)

    with patch.object(adapter, "_make_request", return_value=greenhouse_response):
        raw_jobs = adapter.fetch_jobs(source_config)

    assert len(raw_jobs) == 3  # Truncated from 5 to 3
```

#### Mocking Strategy

- **HTTP Requests**: Mock `requests.Session.request()` or `BaseAdapter._make_request()`
- **Use recorded fixtures**: Real API responses captured during development
- **Test error paths**: Mock exceptions (timeout, connection error, HTTP errors)

### Integration Tests

**File**: `tests/integration/test_adapters_integration.py`

**Scope**: Optional end-to-end tests against real ATS endpoints (if public test companies exist). Mark these tests with `@pytest.mark.integration` and exclude from default test runs.

**Example**:
```python
@pytest.mark.integration
def test_greenhouse_integration_anthropic():
    """Integration test with real Greenhouse endpoint (Anthropic jobs page)."""
    source = SourceConfig(
        name="Anthropic",
        type="greenhouse",
        identifier="anthropic"
    )
    adapter = GreenhouseAdapter(timeout=30)

    raw_jobs = adapter.fetch_jobs(source)

    # Assertions based on expected behavior (not exact counts)
    assert isinstance(raw_jobs, list)
    if len(raw_jobs) > 0:
        assert raw_jobs[0].external_id
        assert raw_jobs[0].title
        assert raw_jobs[0].url.startswith("https://")
```

**Note**: Integration tests may be flaky due to external dependencies. Use sparingly for validation; rely on unit tests with fixtures for CI/CD.

---

## Acceptance Criteria

This implementation satisfies the following acceptance criteria from the PRD and tech spec:

### From User Story 3 (Poll ATS Sources)
- ✅ Greenhouse, Lever, and Ashby adapters call public endpoints with respectful defaults
- ✅ One source failing logs a structured error and does not abort other sources (via factory pattern and error handling)
- ✅ Raw results are normalized into a common `RawJob` structure

### From Step 5 (Implementation Guide)
- ✅ Implement Greenhouse, Lever, Ashby adapters using shared base class
- ✅ Include mapping from ATS-specific fields to `RawJob` domain model
- ✅ Include raw-to-normalized transformation tests with recorded fixtures
- ✅ Shared HTTP request handling with timeout and user-agent
- ✅ HTML cleaning for description fields
- ✅ UTC timestamp parsing and validation

### Additional Criteria
- ✅ Factory pattern for adapter instantiation
- ✅ Custom exception hierarchy for error handling
- ✅ Structured logging for observability
- ✅ Respect `max_jobs_per_source` configuration
- ✅ Comprehensive unit tests with recorded fixtures
- ✅ Integration tests for real API validation (optional)

---

## Implementation Checklist

Use this checklist to track implementation progress:

### 1. Base Infrastructure
- [ ] Create `app/adapters/exceptions.py` with exception hierarchy
- [ ] Create `app/adapters/base.py` with `BaseAdapter` abstract class
- [ ] Implement `_make_request()` helper with error handling
- [ ] Implement `_clean_html()` helper with HTML stripping
- [ ] Implement `_parse_timestamp()` helper with ISO 8601 parsing
- [ ] Add logging configuration and structured log helpers
- [ ] Write unit tests for base utilities

### 2. Greenhouse Adapter
- [ ] Create `app/adapters/greenhouse.py`
- [ ] Implement `GreenhouseAdapter.fetch_jobs()` method
- [ ] Handle field mapping (ID conversion, location, HTML cleaning)
- [ ] Handle error cases (404, 5xx, malformed JSON)
- [ ] Implement max_jobs truncation
- [ ] Create test fixture: `tests/fixtures/ats_responses/greenhouse_sample_response.json`
- [ ] Write unit tests with mocked requests
- [ ] Optional: Write integration test with real endpoint

### 3. Lever Adapter
- [ ] Create `app/adapters/lever.py`
- [ ] Implement `LeverAdapter.fetch_jobs()` method
- [ ] Handle Unix timestamp conversion (milliseconds → seconds)
- [ ] Implement plain text vs HTML fallback logic
- [ ] Handle field mapping (combine description/additional fields)
- [ ] Handle error cases and max_jobs truncation
- [ ] Create test fixture: `tests/fixtures/ats_responses/lever_sample_response.json`
- [ ] Write unit tests with mocked requests
- [ ] Optional: Write integration test with real endpoint

### 4. Ashby Adapter
- [ ] Create `app/adapters/ashby.py`
- [ ] Implement `AshbyAdapter.fetch_jobs()` method
- [ ] Implement GraphQL query construction
- [ ] Handle GraphQL error responses
- [ ] Handle field mapping and HTML cleaning
- [ ] Handle missing jobBoard and error cases
- [ ] Create test fixture: `tests/fixtures/ats_responses/ashby_sample_response.json`
- [ ] Write unit tests with mocked requests
- [ ] Optional: Write integration test with real endpoint

### 5. Factory and Integration
- [ ] Create `app/adapters/factory.py` with `get_adapter()` function
- [ ] Write factory tests for all ATS types
- [ ] Update `app/adapters/__init__.py` with public exports
- [ ] Verify integration with existing domain models (`RawJob`)
- [ ] Verify integration with existing config models (`SourceConfig`, `AdvancedConfig`)
- [ ] Run full test suite and ensure coverage

### 6. Documentation and Validation
- [ ] Add docstrings to all classes and methods
- [ ] Update main README with adapter usage examples
- [ ] Document rate limits and API constraints (if discovered)
- [ ] Validate with real endpoints (at least one per ATS)
- [ ] Create sample configuration with all three ATS types

---

## Known Limitations and Future Work

### Current Limitations (v1.0)
1. **No Pagination**: Adapters fetch all jobs in single request; may hit limits for large companies
2. **No Rate Limiting**: No built-in rate limiting or backoff; relies on ATS tolerance
3. **No Caching**: No ETag or If-Modified-Since support; fetches full response every time
4. **No Authentication**: Ashby adapter assumes public endpoints; may need API key support
5. **No Retry Logic**: Transient errors return empty list; no automatic retry at adapter level
6. **Limited Error Context**: Errors log but don't track failure counts per source

### Future Enhancements (v2.0+)
1. **Pagination Support**: Handle large job lists with pagination (especially Ashby GraphQL)
2. **Conditional Requests**: Use ETag/Last-Modified headers to reduce bandwidth
3. **Rate Limiting**: Implement token bucket or backoff for respectful API usage
4. **Authentication**: Add API key support for Ashby and other ATS requiring auth
5. **Retry Logic**: Exponential backoff for transient errors at adapter level
6. **Observability**: Emit metrics (success rate, latency, job counts) for monitoring
7. **Additional ATS**: Support more ATS platforms (BambooHR, Workday, iCIMS, etc.)
8. **Incremental Fetching**: Only fetch jobs updated since last successful run

---

## Open Questions

1. **Ashby Authentication**: Do all Ashby organizations support public GraphQL endpoints, or do some require API keys? Document if authentication is required.

2. **Rate Limits**: What are the actual rate limits for each ATS? Should we implement proactive rate limiting or rely on 429 response handling?

3. **Pagination Threshold**: At what job count should we implement pagination? Is 1000 jobs a reasonable max_jobs default?

4. **Error Notification**: Should adapter failures trigger any notification (e.g., email after N consecutive failures), or rely on log monitoring? (PRD says no failure notifications in v1.0)

5. **Testing with Real APIs**: Which companies have public job boards we can use for integration testing? Should we maintain a list of "test-friendly" endpoints?

6. **HTML Complexity**: Are there edge cases in HTML cleaning (e.g., tables, images) that need special handling? Should we use a library like `beautifulsoup4` instead of regex?

7. **Timezone Handling**: Do any ATS return non-UTC timestamps? Should adapters handle timezone conversion or assume UTC?

---

## References

### External Documentation
- **Greenhouse API**: https://developers.greenhouse.io/job-board.html
- **Lever API**: https://github.com/lever/postings-api
- **Ashby API**: https://developers.ashbyhq.com/ (if available)

### Internal Documentation
- [docs/job-opportunity-scanner-prd.md](job-opportunity-scanner-prd.md) - Product requirements
- [docs/job-opportunity-scanner-techspec.md](job-opportunity-scanner-techspec.md) - Main technical specification
- [app/domain/models.py](../app/domain/models.py) - Domain model definitions
- [app/config/models.py](../app/config/models.py) - Configuration schema

### Related Implementation Steps
- **Step 4**: Persistence Layer (prerequisite: stores RawJob → Job)
- **Step 6**: Normalization & Matching (next step: processes RawJob → Job with matching rules)
- **Step 8**: Scheduler & Pipeline (integrates adapters into periodic execution)

---

## Summary

This technical specification provides a complete implementation guide for ATS adapters (Step 5). The design prioritizes:

- **Consistency**: Shared base class and uniform error handling
- **Resilience**: Graceful failure handling, structured logging, isolated adapter failures
- **Testability**: Recorded fixtures, comprehensive unit tests, optional integration tests
- **Maintainability**: Clear separation of concerns, factory pattern, extensible for new ATS platforms

Engineers implementing this specification should have all necessary details to:
1. Create the base adapter infrastructure with shared utilities
2. Implement Greenhouse, Lever, and Ashby adapters with correct field mappings
3. Write comprehensive tests using recorded API fixtures
4. Integrate adapters with existing configuration and domain models

**Next Step**: Proceed to Step 6 (Normalization & Matching) to convert `RawJob` → `Job` and apply keyword matching rules.
