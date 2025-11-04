"""Unit tests for ATS adapters."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from app.adapters import (
    AshbyAdapter,
    AdapterConfigurationError,
    AdapterError,
    AdapterHTTPError,
    AdapterResponseError,
    AdapterTimeoutError,
    GreenhouseAdapter,
    LeverAdapter,
    get_adapter,
)
from app.adapters.base import BaseAdapter
from app.config.models import AdvancedConfig, SourceConfig
from app.domain.models import RawJob


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def base_config():
    """Create base advanced config."""
    return AdvancedConfig(
        http_request_timeout=30,
        user_agent="JobOpportunityScanner/1.0",
        max_jobs_per_source=1000,
    )


@pytest.fixture
def greenhouse_config():
    """Create Greenhouse source config."""
    return SourceConfig(
        name="Example Corp",
        type="greenhouse",
        identifier="examplecorp",
    )


@pytest.fixture
def lever_config():
    """Create Lever source config."""
    return SourceConfig(
        name="Example Corp",
        type="lever",
        identifier="examplecorp",
    )


@pytest.fixture
def ashby_config():
    """Create Ashby source config."""
    return SourceConfig(
        name="Example Corp",
        type="ashby",
        identifier="example-org-id",
    )


@pytest.fixture
def greenhouse_response():
    """Load recorded Greenhouse API response."""
    fixture_path = Path(__file__).parent / "fixtures" / "ats_responses" / "greenhouse_sample_response.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def greenhouse_empty_response():
    """Load empty Greenhouse API response."""
    fixture_path = Path(__file__).parent / "fixtures" / "ats_responses" / "greenhouse_empty_response.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def lever_response():
    """Load recorded Lever API response."""
    fixture_path = Path(__file__).parent / "fixtures" / "ats_responses" / "lever_sample_response.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def ashby_response():
    """Load recorded Ashby GraphQL response."""
    fixture_path = Path(__file__).parent / "fixtures" / "ats_responses" / "ashby_sample_response.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def ashby_error_response():
    """Load Ashby GraphQL error response."""
    fixture_path = Path(__file__).parent / "fixtures" / "ats_responses" / "ashby_graphql_error.json"
    with open(fixture_path) as f:
        return json.load(f)


# ============================================================================
# Base Adapter Tests
# ============================================================================


class TestBaseAdapter:
    """Tests for BaseAdapter abstract class."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that BaseAdapter cannot be instantiated directly."""
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BaseAdapter(timeout=30)

    def test_clean_html_strips_tags(self):
        """Test HTML cleaning removes tags."""
        adapter = GreenhouseAdapter(timeout=30)

        html = "<p>This is <strong>bold</strong> text</p>"
        result = adapter._clean_html(html)

        assert result == "This is bold text"

    def test_clean_html_converts_entities(self):
        """Test HTML cleaning converts entities."""
        adapter = GreenhouseAdapter(timeout=30)

        html = "<p>Test &amp; example &nbsp; text</p>"
        result = adapter._clean_html(html)

        assert "&" in result
        assert "example" in result

    def test_clean_html_handles_br_tags(self):
        """Test HTML cleaning preserves line breaks."""
        adapter = GreenhouseAdapter(timeout=30)

        html = "Line 1<br/>Line 2<br>Line 3"
        result = adapter._clean_html(html)

        assert "Line 1\nLine 2\nLine 3" == result

    def test_clean_html_handles_paragraphs(self):
        """Test HTML cleaning preserves paragraph breaks."""
        adapter = GreenhouseAdapter(timeout=30)

        html = "<p>Paragraph 1</p><p>Paragraph 2</p>"
        result = adapter._clean_html(html)

        assert "Paragraph 1" in result
        assert "Paragraph 2" in result
        assert "\n\n" in result

    def test_clean_html_empty_input(self):
        """Test HTML cleaning with empty input."""
        adapter = GreenhouseAdapter(timeout=30)

        result = adapter._clean_html("")
        assert result == ""

    def test_parse_timestamp_valid_iso8601(self):
        """Test timestamp parsing with valid ISO 8601."""
        adapter = GreenhouseAdapter(timeout=30)

        result = adapter._parse_timestamp("2025-11-04T10:30:00Z")

        assert result is not None
        assert result.year == 2025
        assert result.month == 11
        assert result.day == 4
        assert result.hour == 10
        assert result.minute == 30
        assert result.tzinfo == timezone.utc

    def test_parse_timestamp_iso8601_with_milliseconds(self):
        """Test timestamp parsing with milliseconds."""
        adapter = GreenhouseAdapter(timeout=30)

        result = adapter._parse_timestamp("2025-11-04T10:30:00.123Z")

        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_parse_timestamp_none(self):
        """Test timestamp parsing with None."""
        adapter = GreenhouseAdapter(timeout=30)

        result = adapter._parse_timestamp(None)

        assert result is None

    def test_parse_timestamp_empty_string(self):
        """Test timestamp parsing with empty string."""
        adapter = GreenhouseAdapter(timeout=30)

        result = adapter._parse_timestamp("")

        assert result is None

    def test_parse_timestamp_invalid(self):
        """Test timestamp parsing with invalid format."""
        adapter = GreenhouseAdapter(timeout=30)

        result = adapter._parse_timestamp("not-a-timestamp")

        assert result is None

    def test_init_with_invalid_timeout(self):
        """Test adapter initialization with invalid timeout."""
        with pytest.raises(AdapterConfigurationError):
            GreenhouseAdapter(timeout=2)  # Below minimum of 5

    def test_init_with_empty_user_agent(self):
        """Test adapter initialization with empty user agent."""
        with pytest.raises(AdapterConfigurationError):
            GreenhouseAdapter(user_agent="")

    def test_truncate_jobs_respects_limit(self):
        """Test that adapter respects max_jobs limit."""
        adapter = GreenhouseAdapter(timeout=30, max_jobs=3)

        jobs = [
            RawJob(
                external_id=str(i),
                title=f"Job {i}",
                company="Test",
                location="Remote",
                description="Test job",
                url="https://example.com",
            )
            for i in range(5)
        ]

        truncated = adapter._truncate_jobs(jobs, "test_adapter", "test_source")

        assert len(truncated) == 3

    def test_truncate_jobs_unlimited(self):
        """Test that unlimited max_jobs returns all."""
        adapter = GreenhouseAdapter(timeout=30, max_jobs=0)

        jobs = [
            RawJob(
                external_id=str(i),
                title=f"Job {i}",
                company="Test",
                location="Remote",
                description="Test job",
                url="https://example.com",
            )
            for i in range(5)
        ]

        truncated = adapter._truncate_jobs(jobs, "test_adapter", "test_source")

        assert len(truncated) == 5


# ============================================================================
# Greenhouse Adapter Tests
# ============================================================================


class TestGreenhouseAdapter:
    """Tests for GreenhouseAdapter."""

    def test_fetch_jobs_success(self, greenhouse_config, greenhouse_response):
        """Test successful job fetch from Greenhouse."""
        adapter = GreenhouseAdapter(timeout=30)

        with patch.object(adapter, "_make_request", return_value=greenhouse_response):
            raw_jobs = adapter.fetch_jobs(greenhouse_config)

        assert len(raw_jobs) == 5
        assert raw_jobs[0].external_id == "123456"
        assert raw_jobs[0].title == "Senior Software Engineer"
        assert raw_jobs[0].company == "Example Corp"
        assert raw_jobs[0].location == "San Francisco, CA"
        assert raw_jobs[0].updated_at is not None
        assert raw_jobs[0].posted_at is None
        # Check HTML was cleaned
        assert "<p>" not in raw_jobs[0].description
        assert "<ul>" not in raw_jobs[0].description

    def test_fetch_jobs_empty_response(self, greenhouse_config, greenhouse_empty_response):
        """Test fetch with empty jobs array."""
        adapter = GreenhouseAdapter(timeout=30)

        with patch.object(adapter, "_make_request", return_value=greenhouse_empty_response):
            raw_jobs = adapter.fetch_jobs(greenhouse_config)

        assert len(raw_jobs) == 0

    def test_fetch_jobs_missing_location(self, greenhouse_config):
        """Test that missing location is handled gracefully."""
        adapter = GreenhouseAdapter(timeout=30)

        response = {
            "jobs": [
                {
                    "id": 123459,
                    "title": "Engineer",
                    "location": None,
                    "content": "<p>Job description</p>",
                    "absolute_url": "https://example.com/jobs/1",
                    "updated_at": "2025-11-04T10:30:00Z",
                }
            ]
        }

        with patch.object(adapter, "_make_request", return_value=response):
            raw_jobs = adapter.fetch_jobs(greenhouse_config)

        assert len(raw_jobs) == 1
        assert raw_jobs[0].location is None

    def test_fetch_jobs_404_returns_empty_list(self, greenhouse_config):
        """Test that 404 error returns empty list."""
        adapter = GreenhouseAdapter(timeout=30)

        error = AdapterHTTPError("Not Found", status_code=404, url="https://example.com")

        with patch.object(adapter, "_make_request", side_effect=error):
            raw_jobs = adapter.fetch_jobs(greenhouse_config)

        assert len(raw_jobs) == 0

    def test_fetch_jobs_500_returns_empty_list(self, greenhouse_config):
        """Test that 5xx error returns empty list (transient)."""
        adapter = GreenhouseAdapter(timeout=30)

        error = AdapterHTTPError("Server Error", status_code=500, url="https://example.com")

        with patch.object(adapter, "_make_request", side_effect=error):
            raw_jobs = adapter.fetch_jobs(greenhouse_config)

        assert len(raw_jobs) == 0

    def test_fetch_jobs_other_http_error_raises(self, greenhouse_config):
        """Test that other HTTP errors are raised."""
        adapter = GreenhouseAdapter(timeout=30)

        error = AdapterHTTPError("Forbidden", status_code=403, url="https://example.com")

        with patch.object(adapter, "_make_request", side_effect=error):
            with pytest.raises(AdapterHTTPError):
                adapter.fetch_jobs(greenhouse_config)

    def test_fetch_jobs_malformed_response_raises(self, greenhouse_config):
        """Test that malformed response raises error."""
        adapter = GreenhouseAdapter(timeout=30)

        with patch.object(adapter, "_make_request", return_value="invalid"):
            with pytest.raises(AdapterResponseError):
                adapter.fetch_jobs(greenhouse_config)

    def test_fetch_jobs_truncates_to_max(self, greenhouse_config, greenhouse_response):
        """Test that adapter respects max_jobs setting."""
        adapter = GreenhouseAdapter(timeout=30, max_jobs=3)

        with patch.object(adapter, "_make_request", return_value=greenhouse_response):
            raw_jobs = adapter.fetch_jobs(greenhouse_config)

        assert len(raw_jobs) == 3

    def test_fetch_jobs_skips_invalid_jobs(self, greenhouse_config):
        """Test that adapter skips jobs with missing required fields."""
        adapter = GreenhouseAdapter(timeout=30)

        response = {
            "jobs": [
                {
                    "id": 123456,
                    "title": "Valid Job",
                    "location": {"name": "Remote"},
                    "content": "<p>Description</p>",
                    "absolute_url": "https://example.com/1",
                    "updated_at": "2025-11-04T10:30:00Z",
                },
                {
                    "id": 123457,
                    "title": "Missing URL",
                    # Missing required fields
                    "location": {"name": "Remote"},
                    "content": "<p>Description</p>",
                    "updated_at": "2025-11-04T10:30:00Z",
                },
            ]
        }

        with patch.object(adapter, "_make_request", return_value=response):
            raw_jobs = adapter.fetch_jobs(greenhouse_config)

        assert len(raw_jobs) == 1
        assert raw_jobs[0].external_id == "123456"


# ============================================================================
# Lever Adapter Tests
# ============================================================================


class TestLeverAdapter:
    """Tests for LeverAdapter."""

    def test_fetch_jobs_success(self, lever_config, lever_response):
        """Test successful job fetch from Lever."""
        adapter = LeverAdapter(timeout=30)

        with patch.object(adapter, "_make_request", return_value=lever_response):
            raw_jobs = adapter.fetch_jobs(lever_config)

        assert len(raw_jobs) == 5
        assert raw_jobs[0].external_id == "8c8c8c8c-8c8c-8c8c-8c8c-8c8c8c8c8c8c"
        assert raw_jobs[0].title == "Senior Software Engineer"
        assert raw_jobs[0].company == "Example Corp"
        assert raw_jobs[0].location == "San Francisco, CA"
        assert raw_jobs[0].posted_at is not None
        assert raw_jobs[0].updated_at is not None

    def test_fetch_jobs_unix_timestamp_conversion(self, lever_config):
        """Test that Unix timestamps (milliseconds) are converted correctly."""
        adapter = LeverAdapter(timeout=30)

        response = [
            {
                "id": "test-id",
                "text": "Test Job",
                "categories": {"location": "Remote"},
                "descriptionPlain": "Test description",
                "additionalPlain": "",
                "hostedUrl": "https://example.com",
                "createdAt": 1698854400000,  # Unix ms
                "updatedAt": 1699459200000,
            }
        ]

        with patch.object(adapter, "_make_request", return_value=response):
            raw_jobs = adapter.fetch_jobs(lever_config)

        assert len(raw_jobs) == 1
        assert raw_jobs[0].posted_at is not None
        assert raw_jobs[0].updated_at is not None
        assert raw_jobs[0].posted_at.tzinfo == timezone.utc
        assert raw_jobs[0].updated_at.tzinfo == timezone.utc

    def test_fetch_jobs_prefers_plain_text(self, lever_config):
        """Test that plain text descriptions are preferred over HTML."""
        adapter = LeverAdapter(timeout=30)

        response = [
            {
                "id": "test-id",
                "text": "Test Job",
                "categories": {"location": "Remote"},
                "descriptionPlain": "Plain text description",
                "description": "<p>HTML description</p>",
                "additionalPlain": "Additional plain",
                "additional": "<p>Additional HTML</p>",
                "hostedUrl": "https://example.com",
                "createdAt": 1698854400000,
                "updatedAt": 1699459200000,
            }
        ]

        with patch.object(adapter, "_make_request", return_value=response):
            raw_jobs = adapter.fetch_jobs(lever_config)

        assert "Plain text description" in raw_jobs[0].description
        assert "Additional plain" in raw_jobs[0].description
        assert "<p>" not in raw_jobs[0].description

    def test_fetch_jobs_fallback_to_html(self, lever_config):
        """Test fallback to HTML when plain text not available."""
        adapter = LeverAdapter(timeout=30)

        response = [
            {
                "id": "test-id",
                "text": "Test Job",
                "categories": {"location": "Remote"},
                "description": "<p>HTML description</p>",
                "hostedUrl": "https://example.com",
                "createdAt": 1698854400000,
                "updatedAt": 1699459200000,
            }
        ]

        with patch.object(adapter, "_make_request", return_value=response):
            raw_jobs = adapter.fetch_jobs(lever_config)

        assert "HTML description" in raw_jobs[0].description
        assert "<p>" not in raw_jobs[0].description

    def test_fetch_jobs_empty_array_response(self, lever_config):
        """Test handling of empty response array."""
        adapter = LeverAdapter(timeout=30)

        with patch.object(adapter, "_make_request", return_value=[]):
            raw_jobs = adapter.fetch_jobs(lever_config)

        assert len(raw_jobs) == 0

    def test_fetch_jobs_dict_response_fallback(self, lever_config):
        """Test handling of dict response (fallback behavior)."""
        adapter = LeverAdapter(timeout=30)

        response = {
            "postings": [
                {
                    "id": "test-id",
                    "text": "Test Job",
                    "categories": {"location": "Remote"},
                    "descriptionPlain": "Description",
                    "hostedUrl": "https://example.com",
                    "createdAt": 1698854400000,
                    "updatedAt": 1699459200000,
                }
            ]
        }

        with patch.object(adapter, "_make_request", return_value=response):
            raw_jobs = adapter.fetch_jobs(lever_config)

        assert len(raw_jobs) == 1


# ============================================================================
# Ashby Adapter Tests
# ============================================================================


class TestAshbyAdapter:
    """Tests for AshbyAdapter."""

    def test_fetch_jobs_success(self, ashby_config, ashby_response):
        """Test successful job fetch from Ashby."""
        adapter = AshbyAdapter(timeout=30)

        with patch.object(adapter, "_make_request", return_value=ashby_response):
            raw_jobs = adapter.fetch_jobs(ashby_config)

        assert len(raw_jobs) == 5
        assert raw_jobs[0].external_id == "ashby-job-id-001"
        assert raw_jobs[0].title == "Senior Software Engineer"
        assert raw_jobs[0].company == "Example Corp"
        assert raw_jobs[0].location == "San Francisco, CA"
        assert raw_jobs[0].posted_at is not None
        assert raw_jobs[0].updated_at is not None
        # Check HTML was cleaned
        assert "<p>" not in raw_jobs[0].description

    def test_fetch_jobs_graphql_error(self, ashby_config, ashby_error_response):
        """Test handling of GraphQL error response."""
        adapter = AshbyAdapter(timeout=30)

        with patch.object(adapter, "_make_request", return_value=ashby_error_response):
            with pytest.raises(AdapterResponseError, match="GraphQL errors"):
                adapter.fetch_jobs(ashby_config)

    def test_fetch_jobs_missing_data_field(self, ashby_config):
        """Test handling when data field is missing."""
        adapter = AshbyAdapter(timeout=30)

        response = {"data": None}

        with patch.object(adapter, "_make_request", return_value=response):
            raw_jobs = adapter.fetch_jobs(ashby_config)

        assert len(raw_jobs) == 0

    def test_fetch_jobs_missing_job_board(self, ashby_config):
        """Test handling when jobBoard is missing."""
        adapter = AshbyAdapter(timeout=30)

        response = {"data": {"jobBoard": None}}

        with patch.object(adapter, "_make_request", return_value=response):
            raw_jobs = adapter.fetch_jobs(ashby_config)

        assert len(raw_jobs) == 0

    def test_fetch_jobs_null_location(self, ashby_config):
        """Test that null location is handled gracefully."""
        adapter = AshbyAdapter(timeout=30)

        response = {
            "data": {
                "jobBoard": {
                    "jobPostings": [
                        {
                            "id": "test-id",
                            "title": "Engineer",
                            "location": None,
                            "description": "<p>Description</p>",
                            "externalLink": "https://example.com",
                            "publishedDate": "2025-11-01T12:00:00.000Z",
                            "updatedAt": "2025-11-04T10:30:00.000Z",
                        }
                    ]
                }
            }
        }

        with patch.object(adapter, "_make_request", return_value=response):
            raw_jobs = adapter.fetch_jobs(ashby_config)

        assert len(raw_jobs) == 1
        assert raw_jobs[0].location is None

    def test_fetch_jobs_404_returns_empty_list(self, ashby_config):
        """Test that 404 error returns empty list."""
        adapter = AshbyAdapter(timeout=30)

        error = AdapterHTTPError("Not Found", status_code=404, url="https://example.com")

        with patch.object(adapter, "_make_request", side_effect=error):
            raw_jobs = adapter.fetch_jobs(ashby_config)

        assert len(raw_jobs) == 0


# ============================================================================
# Factory Tests
# ============================================================================


class TestAdapterFactory:
    """Tests for the adapter factory function."""

    def test_factory_creates_greenhouse_adapter(self, greenhouse_config, base_config):
        """Test factory creates GreenhouseAdapter for 'greenhouse' type."""
        adapter = get_adapter(greenhouse_config, base_config)

        assert isinstance(adapter, GreenhouseAdapter)
        assert adapter.timeout == base_config.http_request_timeout
        assert adapter.user_agent == base_config.user_agent
        assert adapter.max_jobs == base_config.max_jobs_per_source

    def test_factory_creates_lever_adapter(self, lever_config, base_config):
        """Test factory creates LeverAdapter for 'lever' type."""
        adapter = get_adapter(lever_config, base_config)

        assert isinstance(adapter, LeverAdapter)

    def test_factory_creates_ashby_adapter(self, ashby_config, base_config):
        """Test factory creates AshbyAdapter for 'ashby' type."""
        adapter = get_adapter(ashby_config, base_config)

        assert isinstance(adapter, AshbyAdapter)

    def test_factory_invalid_type_raises_error(self, base_config):
        """Test factory raises error for invalid ATS type.

        Note: SourceConfig validates ATS type at Pydantic level,
        so we mock an invalid type to reach the factory validation.
        """
        # Create a valid config first, then patch the type
        valid_config = SourceConfig(
            name="Test",
            type="greenhouse",
            identifier="test",
        )

        # Mock the type attribute to be invalid
        valid_config.type = "invalid_ats"

        with pytest.raises(AdapterConfigurationError, match="Unknown ATS type"):
            get_adapter(valid_config, base_config)

    def test_factory_passes_advanced_config(self, greenhouse_config, base_config):
        """Test factory passes advanced config to adapter."""
        custom_config = AdvancedConfig(
            http_request_timeout=60,
            user_agent="CustomAgent/2.0",
            max_jobs_per_source=500,
        )

        adapter = get_adapter(greenhouse_config, custom_config)

        assert adapter.timeout == 60
        assert adapter.user_agent == "CustomAgent/2.0"
        assert adapter.max_jobs == 500


# ============================================================================
# Exception Tests
# ============================================================================


class TestAdapterExceptions:
    """Tests for adapter exception handling."""

    def test_adapter_http_error_attributes(self):
        """Test AdapterHTTPError stores status and URL."""
        error = AdapterHTTPError("Test error", status_code=404, url="https://example.com")

        assert error.status_code == 404
        assert error.url == "https://example.com"

    def test_adapter_timeout_error_attributes(self):
        """Test AdapterTimeoutError stores URL."""
        error = AdapterTimeoutError("Test timeout", url="https://example.com")

        assert error.url == "https://example.com"

    def test_exception_inheritance(self):
        """Test exception hierarchy."""
        assert issubclass(AdapterHTTPError, AdapterError)
        assert issubclass(AdapterTimeoutError, AdapterError)
        assert issubclass(AdapterResponseError, AdapterError)
        assert issubclass(AdapterConfigurationError, AdapterError)
