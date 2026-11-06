"""Unit tests for the enhanced LocationFilter.

Tests for:
- Region-based filtering (Americas vs EMEA/APAC)
- Canada exception with US eligibility phrases
- Hybrid handling for specific allowed cities
- Description-based remote fallback
- Structured remote signal requirements
"""

import pytest
from datetime import datetime, timezone

from app.config.models import FilteringRules
from app.domain.models import Job
from app.filtering.location_filter import LocationFilter
from app.filtering.models import FilterContext, FilterResult


@pytest.fixture
def default_rules():
    """Create default filtering rules."""
    return FilteringRules()


@pytest.fixture
def filter_context(default_rules):
    """Create a filter context with default rules."""
    return FilterContext(
        filtering_rules=default_rules,
        scan_timestamp=datetime.now(timezone.utc),
        run_id="test-run-001",
    )


@pytest.fixture
def base_job():
    """Create a base job for testing."""
    return Job(
        job_key="test-job-001",
        source_type="greenhouse",
        source_identifier="test-company",
        external_id="12345",
        title="Software Engineer",
        company="Test Corp",
        location="Remote",
        description="Looking for a software engineer.",
        url="https://example.com/job/12345",
        posted_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        content_hash="abc123",
    )


class TestBasicRemoteEligibility:
    """Tests for basic remote eligibility detection."""

    def test_explicit_is_remote_flag(self, base_job, filter_context):
        """Test that explicit is_remote=True flag passes."""
        base_job.is_remote = True
        base_job.location = "New York, NY"

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.is_remote_eligible is True
        assert result.remote_signal_source == "is_remote_flag"

    def test_remote_in_primary_location(self, base_job, filter_context):
        """Test that 'Remote' in primary location passes."""
        base_job.location = "Remote"

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.is_remote_eligible is True
        assert result.remote_signal_source == "primary_location_match"

    def test_remote_us_in_location(self, base_job, filter_context):
        """Test that 'Remote - US' in location passes."""
        base_job.location = "Remote - US"

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.is_remote_eligible is True

    def test_remote_in_secondary_locations(self, base_job, filter_context):
        """Test that 'Remote' in secondary locations passes."""
        base_job.location = "San Francisco, CA"
        base_job.secondary_locations = ["Remote", "New York, NY"]

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.remote_signal_source == "secondary_location_match"

    def test_onsite_only_fails(self, base_job, filter_context):
        """Test that on-site only location fails."""
        base_job.location = "San Francisco, CA"
        base_job.is_remote = False

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "location_not_remote"

    def test_missing_location_data_fails(self, base_job, filter_context):
        """Test that missing all location data fails."""
        base_job.location = None
        base_job.secondary_locations = []
        base_job.is_remote = None

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "location_missing"


class TestRegionFiltering:
    """Tests for region-based filtering."""

    def test_americas_region_allowed(self, base_job, filter_context):
        """Test that 'Americas' region in location passes."""
        base_job.location = "Remote - Americas"

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert "americas" in result.remote_signal_source.lower()

    def test_emea_region_blocked(self, base_job, filter_context):
        """Test that 'EMEA' region in location is blocked."""
        base_job.location = "Remote - EMEA"
        base_job.is_remote = True

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "non_americas_remote"

    def test_apac_region_blocked(self, base_job, filter_context):
        """Test that 'APAC' region in location is blocked."""
        base_job.location = "Remote - APAC"
        base_job.is_remote = True

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "non_americas_remote"

    def test_uk_region_blocked(self, base_job, filter_context):
        """Test that 'UK' region in location is blocked."""
        base_job.location = "Remote - UK"
        base_job.is_remote = True

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "non_americas_remote"

    def test_india_region_blocked(self, base_job, filter_context):
        """Test that 'India' region in location is blocked."""
        base_job.location = "Remote - India"
        base_job.is_remote = True

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "non_americas_remote"


class TestCanadaException:
    """Tests for Canada exception with US eligibility."""

    def test_canada_blocked_without_us_eligibility(self, base_job, filter_context):
        """Test that Canada remote is blocked without US eligibility phrase."""
        base_job.location = "Remote - Canada"
        base_job.is_remote = True
        base_job.description = "We are looking for a Canadian developer."

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "non_americas_remote"

    def test_canada_allowed_with_us_or_canada_phrase(self, base_job, filter_context):
        """Test that Canada remote is allowed with 'US or Canada' phrase."""
        base_job.location = "Remote - Canada"
        base_job.is_remote = True
        base_job.description = "This role is open to candidates in US or Canada."

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.canada_exception_applied is True

    def test_canada_allowed_with_north_america_phrase(self, base_job, filter_context):
        """Test that Canada remote is allowed with 'North America' phrase."""
        base_job.location = "Remote - Canada"
        base_job.is_remote = True
        base_job.description = "Remote position available for North America."

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.canada_exception_applied is True

    def test_canada_exception_disabled(self, base_job, default_rules):
        """Test Canada exception can be disabled."""
        default_rules.allow_remote_canada_if_us_allowed = False
        filter_context = FilterContext(
            filtering_rules=default_rules,
            scan_timestamp=datetime.now(timezone.utc),
            run_id="test-run-001",
        )
        base_job.location = "Remote - Canada"
        base_job.is_remote = True
        base_job.description = "This role is open to US or Canada."

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        # Should still be blocked because exception is disabled
        assert result.should_process is False


class TestHybridHandling:
    """Tests for hybrid work location handling."""

    def test_hybrid_philadelphia_allowed(self, base_job, filter_context):
        """Test that hybrid in Philadelphia is allowed."""
        base_job.location = "Hybrid - Philadelphia, PA"

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.hybrid_allowed is True
        assert result.remote_signal_source == "hybrid_city_match"

    def test_hybrid_non_allowed_city_blocked(self, base_job, filter_context):
        """Test that hybrid in non-allowed city is blocked."""
        base_job.location = "Hybrid - New York, NY"

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "hybrid_not_allowed"
        assert result.hybrid_allowed is False

    def test_hybrid_san_francisco_blocked(self, base_job, filter_context):
        """Test that hybrid in San Francisco is blocked."""
        base_job.location = "Hybrid - San Francisco, CA"

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "hybrid_not_allowed"

    def test_custom_hybrid_city_allowed(self, base_job, default_rules):
        """Test that custom hybrid cities can be configured."""
        default_rules.allow_hybrid_cities = ["boston, ma", "boston"]
        filter_context = FilterContext(
            filtering_rules=default_rules,
            scan_timestamp=datetime.now(timezone.utc),
            run_id="test-run-001",
        )
        base_job.location = "Hybrid - Boston, MA"

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.hybrid_allowed is True


class TestDescriptionFallback:
    """Tests for description-based remote detection fallback."""

    def test_description_fallback_disabled_by_default(self, base_job, filter_context):
        """Test that description fallback is disabled when require_remote_structured=True."""
        base_job.location = "San Francisco, CA"
        base_job.is_remote = None
        base_job.description = "This is a fully remote position."

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        # Should fail because structured signals are required
        assert result.should_process is False

    def test_description_fallback_when_enabled(self, base_job, default_rules):
        """Test description fallback when enabled."""
        default_rules.require_remote_structured = False
        default_rules.remote_description_keywords = ["fully remote", "work from anywhere"]
        filter_context = FilterContext(
            filtering_rules=default_rules,
            scan_timestamp=datetime.now(timezone.utc),
            run_id="test-run-001",
        )
        base_job.location = "San Francisco, CA"
        base_job.is_remote = None
        base_job.description = "This is a fully remote position with flexible hours."

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.remote_signal_source == "description_fallback"


class TestCountryFiltering:
    """Tests for country-based filtering."""

    def test_us_country_allowed(self, base_job, filter_context):
        """Test that US country passes."""
        base_job.location = "Remote"
        base_job.location_country = "United States"

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.location_country == "United States"

    def test_germany_country_blocked(self, base_job, filter_context):
        """Test that non-US country is blocked."""
        base_job.location = "Remote"
        base_job.location_country = "Germany"

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "location_country_not_acceptable"

    def test_canada_country_with_exception(self, base_job, filter_context):
        """Test Canada country passes with US eligibility exception."""
        base_job.location = "Remote - Canada"
        base_job.location_country = "Canada"
        base_job.is_remote = True
        base_job.description = "Open to US or Canada applicants."

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.canada_exception_applied is True


class TestRemoteNotRequired:
    """Tests for when remote requirement is disabled."""

    def test_onsite_allowed_when_remote_not_required(self, base_job, default_rules):
        """Test that on-site jobs pass when require_remote=False."""
        default_rules.require_remote = False
        filter_context = FilterContext(
            filtering_rules=default_rules,
            scan_timestamp=datetime.now(timezone.utc),
            run_id="test-run-001",
        )
        base_job.location = "San Francisco, CA"
        base_job.is_remote = False

        location_filter = LocationFilter()
        result = location_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.filter_metadata.get("location_filter") == "remote_not_required"
