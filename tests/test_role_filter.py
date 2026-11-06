"""Unit tests for the RoleFilter.

Tests for:
- Seniority term blocking (senior, sr, staff, principal, etc.)
- Seniority allow terms override
- Role family filtering (developer/engineer vs non-engineering)
- Years of experience extraction and filtering
- Feature toggle behavior (enable/disable filters)
"""

import pytest
from datetime import datetime, timezone

from app.config.models import FilteringRules
from app.domain.models import Job
from app.filtering.role_filter import RoleFilter
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
        description="Looking for a software engineer with 3-5 years of experience.",
        url="https://example.com/job/12345",
        posted_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        content_hash="abc123",
    )


class TestSeniorityFiltering:
    """Tests for seniority-based filtering."""

    def test_blocks_senior_title(self, base_job, filter_context):
        """Test that 'Senior' in title is blocked."""
        base_job.title = "Senior Software Engineer"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "role_filter_seniority_blocked"
        assert result.seniority_blocked is True

    def test_blocks_sr_abbreviation(self, base_job, filter_context):
        """Test that 'Sr' abbreviation is blocked."""
        base_job.title = "Sr. Software Engineer"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.seniority_blocked is True

    def test_blocks_staff_title(self, base_job, filter_context):
        """Test that 'Staff' in title is blocked."""
        base_job.title = "Staff Engineer"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.seniority_blocked is True

    def test_blocks_principal_title(self, base_job, filter_context):
        """Test that 'Principal' in title is blocked."""
        base_job.title = "Principal Software Engineer"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.seniority_blocked is True

    def test_blocks_lead_title(self, base_job, filter_context):
        """Test that 'Lead' in title is blocked."""
        base_job.title = "Lead Developer"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.seniority_blocked is True

    def test_blocks_manager_title(self, base_job, filter_context):
        """Test that 'Manager' in title is blocked."""
        base_job.title = "Engineering Manager"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.seniority_blocked is True

    def test_blocks_director_title(self, base_job, filter_context):
        """Test that 'Director' in title is blocked."""
        base_job.title = "Director of Engineering"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.seniority_blocked is True

    def test_allows_mid_level_title(self, base_job, filter_context):
        """Test that mid-level titles pass."""
        base_job.title = "Software Engineer"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.seniority_blocked is False

    def test_allows_junior_title(self, base_job, filter_context):
        """Test that junior titles pass."""
        base_job.title = "Junior Developer"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.seniority_blocked is False

    def test_seniority_allow_terms_override(self, base_job, default_rules):
        """Test that seniority_allow_terms can override blocking."""
        default_rules.seniority_allow_terms = ["senior associate"]
        filter_context = FilterContext(
            filtering_rules=default_rules,
            scan_timestamp=datetime.now(timezone.utc),
            run_id="test-run-001",
        )
        base_job.title = "Senior Associate Engineer"

        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        # "senior associate" is in the title, so it should pass (allow term overrides)
        assert result.should_process is True
        assert result.seniority_blocked is False

    def test_disabled_seniority_filter(self, base_job, default_rules):
        """Test that disabled seniority filter allows senior titles."""
        default_rules.enable_seniority_filter = False
        filter_context = FilterContext(
            filtering_rules=default_rules,
            scan_timestamp=datetime.now(timezone.utc),
            run_id="test-run-001",
        )
        base_job.title = "Senior Software Engineer"

        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True


class TestRoleFamilyFiltering:
    """Tests for role family filtering."""

    def test_allows_developer_title(self, base_job, filter_context):
        """Test that 'Developer' in title is allowed."""
        base_job.title = "Software Developer"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.role_blocked is False

    def test_allows_engineer_title(self, base_job, filter_context):
        """Test that 'Engineer' in title is allowed."""
        base_job.title = "Software Engineer"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.role_blocked is False

    def test_allows_engineering_title(self, base_job, filter_context):
        """Test that 'Engineering' in title is allowed."""
        base_job.title = "Full Stack Engineering"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True

    def test_blocks_recruiter_title(self, base_job, filter_context):
        """Test that 'Recruiter' in title is blocked."""
        base_job.title = "Technical Recruiter"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "role_filter_non_engineering"
        assert result.role_blocked is True

    def test_blocks_hr_title(self, base_job, filter_context):
        """Test that 'HR' in title is blocked."""
        base_job.title = "HR Coordinator"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.role_blocked is True

    def test_blocks_sales_title(self, base_job, filter_context):
        """Test that 'Sales' in title is blocked."""
        base_job.title = "Sales Engineer"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        # Sales Engineer contains both "Sales" (blocked) and "Engineer" (allowed)
        # Should be blocked because blocked terms take precedence
        assert result.should_process is False
        assert result.role_blocked is True

    def test_blocks_support_title(self, base_job, filter_context):
        """Test that 'Support' in title is blocked."""
        base_job.title = "Customer Support"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.role_blocked is True

    def test_blocks_marketing_title(self, base_job, filter_context):
        """Test that 'Marketing' in title is blocked."""
        base_job.title = "Marketing Specialist"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.role_blocked is True

    def test_blocks_missing_engineering_term(self, base_job, filter_context):
        """Test that titles without developer/engineer are blocked."""
        base_job.title = "Product Manager"
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        # "Manager" is blocked by seniority, and "Product Manager" doesn't have engineer
        assert result.should_process is False

    def test_disabled_role_filter(self, base_job, default_rules):
        """Test that disabled role filter allows non-engineering titles."""
        default_rules.enable_role_filter = False
        default_rules.enable_seniority_filter = False  # Disable both for clean test
        filter_context = FilterContext(
            filtering_rules=default_rules,
            scan_timestamp=datetime.now(timezone.utc),
            run_id="test-run-001",
        )
        base_job.title = "Product Manager"

        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True


class TestExperienceFiltering:
    """Tests for years of experience filtering."""

    def test_allows_5_years_experience(self, base_job, filter_context):
        """Test that 5 years experience passes."""
        base_job.description = "Looking for a developer with 5 years of experience."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.detected_years_experience == 5

    def test_allows_3_to_5_years_range(self, base_job, filter_context):
        """Test that 3-5 years range passes (takes minimum)."""
        base_job.description = "Requires 3-5 years of experience in software development."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.detected_years_experience == 3

    def test_blocks_10_plus_years(self, base_job, filter_context):
        """Test that 10+ years experience is blocked."""
        base_job.description = "Looking for an engineer with 10+ years of experience."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.filter_reason == "role_filter_experience_blocked"
        assert result.experience_blocked is True
        assert result.detected_years_experience == 10

    def test_blocks_minimum_10_years(self, base_job, filter_context):
        """Test that 'minimum 10 years' is blocked."""
        base_job.description = "Must have minimum 10 years experience in the field."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.experience_blocked is True

    def test_allows_8_years_soft_threshold(self, base_job, filter_context):
        """Test that 8 years (soft threshold) passes."""
        base_job.description = "Requires 8 years of experience in Python."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.detected_years_experience == 8

    def test_blocks_9_years_hard_block(self, base_job, filter_context):
        """Test that 9 years (hard block default) is blocked."""
        base_job.description = "We need 9 years of experience in the industry."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.detected_years_experience == 9

    def test_no_experience_mentioned(self, base_job, filter_context):
        """Test that jobs without experience requirements pass."""
        base_job.description = "Looking for a great developer to join our team."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True
        assert result.detected_years_experience is None

    def test_custom_hard_block_threshold(self, base_job, default_rules):
        """Test custom hard block threshold."""
        default_rules.hard_block_years = 12
        filter_context = FilterContext(
            filtering_rules=default_rules,
            scan_timestamp=datetime.now(timezone.utc),
            run_id="test-run-001",
        )
        base_job.description = "Requires 10 years of experience."

        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        # 10 is below hard block of 12
        assert result.should_process is True

    def test_disabled_experience_filter(self, base_job, default_rules):
        """Test that disabled experience filter allows high experience requirements."""
        default_rules.enable_experience_filter = False
        filter_context = FilterContext(
            filtering_rules=default_rules,
            scan_timestamp=datetime.now(timezone.utc),
            run_id="test-run-001",
        )
        base_job.description = "Requires 15 years of experience."

        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is True


class TestExperiencePatternMatching:
    """Tests for experience pattern extraction from descriptions."""

    def test_pattern_x_plus_years(self, base_job, filter_context):
        """Test '5+ years of experience' pattern."""
        base_job.description = "5+ years of experience required."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)
        assert result.detected_years_experience == 5

    def test_pattern_x_years_exp(self, base_job, filter_context):
        """Test '5 years exp' pattern."""
        base_job.description = "Must have 5 years exp in Python."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)
        assert result.detected_years_experience == 5

    def test_pattern_minimum_x_years(self, base_job, filter_context):
        """Test 'minimum X years' pattern."""
        base_job.description = "Minimum 6 years in software development."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)
        assert result.detected_years_experience == 6

    def test_pattern_at_least_x_years(self, base_job, filter_context):
        """Test 'at least X years' pattern."""
        base_job.description = "At least 4 years of professional experience."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)
        assert result.detected_years_experience == 4

    def test_pattern_x_to_y_years(self, base_job, filter_context):
        """Test 'X to Y years' pattern (takes minimum)."""
        base_job.description = "7 to 10 years of relevant experience."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)
        assert result.detected_years_experience == 7

    def test_pattern_x_dash_y_years(self, base_job, filter_context):
        """Test 'X-Y years' pattern (takes minimum)."""
        base_job.description = "4-6 years experience preferred."
        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)
        assert result.detected_years_experience == 4


class TestCombinedFilters:
    """Tests for combined filter scenarios."""

    def test_senior_engineer_blocked_by_seniority(self, base_job, filter_context):
        """Test Senior Software Engineer is blocked by seniority despite having 'Engineer'."""
        base_job.title = "Senior Software Engineer"
        base_job.description = "Looking for a senior engineer with 5 years of experience."

        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        # Should be blocked by seniority, not role
        assert result.should_process is False
        assert result.seniority_blocked is True

    def test_mid_level_with_high_experience_blocked(self, base_job, filter_context):
        """Test mid-level title blocked by high experience requirement."""
        base_job.title = "Software Engineer"
        base_job.description = "Must have 10+ years of experience in the field."

        role_filter = RoleFilter()
        result = role_filter.evaluate(base_job, filter_context)

        assert result.should_process is False
        assert result.experience_blocked is True
        # seniority_blocked is None because experience filter fails first,
        # indicating it wasn't blocked by seniority
        assert result.seniority_blocked is None or result.seniority_blocked is False
