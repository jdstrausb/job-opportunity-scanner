"""Tests for digest builder functionality."""

import pytest
from datetime import datetime, timedelta, timezone

from app.domain.models import Job
from app.matching.models import CandidateMatch, MatchResult
from app.normalization.models import MatchableText, NormalizationResult
from app.notifications.digest import CompanyGroup, DigestBuilder


@pytest.fixture
def sample_job():
    """Create a sample job for testing."""
    return Job(
        job_key="test-job-1",
        source_type="greenhouse",
        source_identifier="test-company",
        external_id="12345",
        title="Software Engineer",
        company="Test Company",
        location="San Francisco, CA",
        description="Great opportunity",
        url="https://example.com/job/12345",
        posted_at=datetime(2025, 11, 10, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2025, 11, 10, 12, 0, 0, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 11, 10, 12, 0, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2025, 11, 10, 12, 0, 0, tzinfo=timezone.utc),
        content_hash="abc123",
    )


@pytest.fixture
def sample_match_result():
    """Create a sample match result."""
    return MatchResult(
        is_match=True,
        matched_required_terms={"python", "docker"},
        missing_required_terms=set(),
        matched_keyword_groups=[{"backend"}, {"postgresql"}],
        missing_keyword_groups=[],
        matched_exclude_terms=set(),
        matched_fields={"description": {"python", "docker"}},
        snippets=["Experience with Python and Docker"],
        summary="Matched: python, docker, backend, postgresql"
    )


@pytest.fixture
def sample_raw_job():
    """Create a sample raw job."""
    from app.domain.models import RawJob
    return RawJob(
        external_id="12345",
        title="Software Engineer",
        company="Test Company",
        location="San Francisco, CA",
        description="Great opportunity",
        url="https://example.com/job/12345",
        posted_at=datetime(2025, 11, 10, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2025, 11, 10, 12, 0, 0, tzinfo=timezone.utc),
    )


def create_candidate_match(job, match_result=None, raw_job=None):
    """Helper function to create a candidate match for a job."""
    if match_result is None:
        match_result = MatchResult(
            is_match=True,
            matched_required_terms={"python"},
            missing_required_terms=set(),
            matched_keyword_groups=[{"backend"}],
            missing_keyword_groups=[],
            matched_exclude_terms=set(),
            matched_fields={"description": {"python"}},
            snippets=[],
            summary="Test match"
        )

    if raw_job is None:
        from app.domain.models import RawJob
        raw_job = RawJob(
            external_id=job.external_id,
            title=job.title,
            company=job.company or "Unknown",  # RawJob doesn't allow None
            location=job.location,
            description=job.description,
            url=job.url,
            posted_at=job.posted_at,
            updated_at=job.updated_at,
        )

    norm_result = NormalizationResult(
        job=job,
        existing_job=None,
        is_new=True,
        content_changed=True,
        matchable_text=MatchableText(
            title_original=job.title,
            title_normalized=job.title.lower(),
            description_original=job.description,
            description_normalized=job.description.lower(),
            location_original=job.location or "",
            location_normalized=(job.location or "").lower(),
            full_text_normalized=f"{job.title} {job.description}".lower()
        ),
        raw_job=raw_job
    )
    return CandidateMatch(
        normalization_result=norm_result,
        match_result=match_result
    )


@pytest.fixture
def sample_candidate_match(sample_job, sample_match_result, sample_raw_job):
    """Create a sample candidate match."""
    return create_candidate_match(sample_job, sample_match_result, sample_raw_job)


class TestCompanyGroup:
    """Tests for CompanyGroup class."""

    def test_init(self):
        """Test CompanyGroup initialization."""
        group = CompanyGroup("Test Company")
        assert group.company_name == "Test Company"
        assert group.jobs == []
        assert group.most_recent_date is None

    def test_add_job_updates_most_recent(self, sample_candidate_match):
        """Test adding job updates most_recent_date."""
        group = CompanyGroup("Test Company")
        group.add_job(sample_candidate_match)

        assert len(group.jobs) == 1
        assert group.most_recent_date == sample_candidate_match.job.posted_at

    def test_add_job_without_posted_at(self, sample_candidate_match):
        """Test adding job without posted_at doesn't update most_recent_date."""
        sample_candidate_match.job.posted_at = None
        group = CompanyGroup("Test Company")
        group.add_job(sample_candidate_match)

        assert len(group.jobs) == 1
        assert group.most_recent_date is None

    def test_add_multiple_jobs_tracks_newest(self, sample_job):
        """Test adding multiple jobs tracks the newest date."""
        group = CompanyGroup("Test Company")

        # Add job with older date
        job1 = sample_job.model_copy()
        job1.posted_at = datetime(2025, 11, 5, tzinfo=timezone.utc)
        job1.job_key = "job-1"
        candidate1 = create_candidate_match(job1)
        group.add_job(candidate1)

        # Add job with newer date
        job2 = sample_job.model_copy()
        job2.posted_at = datetime(2025, 11, 10, tzinfo=timezone.utc)
        job2.job_key = "job-2"
        candidate2 = create_candidate_match(job2)
        group.add_job(candidate2)

        assert group.most_recent_date == datetime(2025, 11, 10, tzinfo=timezone.utc)

    def test_sort_jobs_by_date(self, sample_job):
        """Test sort_jobs_by_date sorts descending."""
        group = CompanyGroup("Test Company")

        # Add jobs in random order
        dates = [
            datetime(2025, 11, 5, tzinfo=timezone.utc),
            datetime(2025, 11, 10, tzinfo=timezone.utc),
            datetime(2025, 11, 8, tzinfo=timezone.utc),
        ]

        for i, date in enumerate(dates):
            job = sample_job.model_copy()
            job.posted_at = date
            job.job_key = f"job-{i}"
            candidate = create_candidate_match(job)
            group.add_job(candidate)

        group.sort_jobs_by_date()

        # Should be sorted newest first
        assert group.jobs[0].job.posted_at == datetime(2025, 11, 10, tzinfo=timezone.utc)
        assert group.jobs[1].job.posted_at == datetime(2025, 11, 8, tzinfo=timezone.utc)
        assert group.jobs[2].job.posted_at == datetime(2025, 11, 5, tzinfo=timezone.utc)


class TestDigestBuilder:
    """Tests for DigestBuilder class."""

    def test_group_by_company_empty_list(self):
        """Test grouping empty list returns empty dict."""
        builder = DigestBuilder()
        groups = builder.group_by_company([])
        assert groups == {}

    def test_group_by_company_single_company(self, sample_candidate_match):
        """Test grouping jobs from single company."""
        builder = DigestBuilder()
        matches = [sample_candidate_match]
        groups = builder.group_by_company(matches)

        assert len(groups) == 1
        assert "Test Company" in groups
        assert len(groups["Test Company"].jobs) == 1

    def test_group_by_company_multiple_companies(self, sample_job):
        """Test grouping jobs from multiple companies."""
        builder = DigestBuilder()

        # Create matches from different companies
        matches = []
        for company in ["Company A", "Company B", "Company A"]:
            job = sample_job.model_copy()
            job.company = company
            job.job_key = f"job-{len(matches)}"
            candidate = create_candidate_match(job)
            matches.append(candidate)

        groups = builder.group_by_company(matches)

        assert len(groups) == 2
        assert len(groups["Company A"].jobs) == 2
        assert len(groups["Company B"].jobs) == 1

    def test_group_by_company_none_company(self, sample_job):
        """Test grouping jobs with None company uses 'Unknown Company'."""
        builder = DigestBuilder()

        job = sample_job.model_copy()
        job.company = None
        candidate = create_candidate_match(job)

        groups = builder.group_by_company([candidate])

        assert "Unknown Company" in groups
        assert len(groups["Unknown Company"].jobs) == 1

    def test_build_digest_context_empty(self):
        """Test building context with no matches."""
        builder = DigestBuilder()
        context = builder.build_digest_context([], "scan-123")

        assert context["scan_id"] == "scan-123"
        assert context["total_matches"] == 0
        assert context["total_companies"] == 0
        assert context["companies"] == []

    def test_build_digest_context_structure(self, sample_candidate_match):
        """Test digest context has all required fields."""
        builder = DigestBuilder()
        context = builder.build_digest_context([sample_candidate_match], "scan-123")

        # Top-level fields
        assert "scan_id" in context
        assert "scan_date" in context
        assert "total_matches" in context
        assert "total_companies" in context
        assert "companies" in context

        # Company fields
        assert len(context["companies"]) == 1
        company = context["companies"][0]
        assert "name" in company
        assert "job_count" in company
        assert "most_recent_date" in company
        assert "jobs" in company

        # Job fields
        assert len(company["jobs"]) == 1
        job = company["jobs"][0]
        assert "title" in job
        assert "location" in job
        assert "url" in job
        assert "posted_at" in job
        assert "match_quality" in job
        assert "match_summary" in job
        assert "matched_terms" in job
        assert "job_key" in job

    def test_build_digest_context_company_sort(self, sample_job):
        """Test companies sorted by most recent date."""
        builder = DigestBuilder()

        # Create matches from different companies with different dates
        matches = []
        companies_dates = [
            ("Company A", datetime(2025, 11, 5, tzinfo=timezone.utc)),
            ("Company B", datetime(2025, 11, 10, tzinfo=timezone.utc)),
            ("Company C", datetime(2025, 11, 8, tzinfo=timezone.utc)),
        ]

        for company, date in companies_dates:
            job = sample_job.model_copy()
            job.company = company
            job.posted_at = date
            job.job_key = f"job-{company}"
            candidate = create_candidate_match(job)
            matches.append(candidate)

        context = builder.build_digest_context(matches, "scan-123")

        # Companies should be sorted by newest first
        assert context["companies"][0]["name"] == "Company B"  # Nov 10
        assert context["companies"][1]["name"] == "Company C"  # Nov 8
        assert context["companies"][2]["name"] == "Company A"  # Nov 5

    def test_build_job_summary_limits_matched_terms(self, sample_candidate_match):
        """Test job summary limits matched terms to first 3."""
        builder = DigestBuilder()

        # Add more terms
        sample_candidate_match.match_result.matched_required_terms = {
            "python", "docker", "kubernetes", "postgresql", "redis"
        }

        summary = builder._build_job_summary(sample_candidate_match)

        # Should only have 3 terms
        assert len(summary["matched_terms"]) == 3

    def test_build_job_summary_extracts_first_line(self, sample_candidate_match):
        """Test job summary extracts only first line of summary."""
        builder = DigestBuilder()

        # Multi-line summary
        sample_candidate_match.match_result.summary = "First line\nSecond line\nThird line"

        summary = builder._build_job_summary(sample_candidate_match)

        assert summary["match_summary"] == "First line"

    def test_build_job_summary_handles_none_location(self, sample_candidate_match):
        """Test job summary handles None location."""
        builder = DigestBuilder()

        sample_candidate_match.job.location = None

        summary = builder._build_job_summary(sample_candidate_match)

        assert summary["location"] == "Location not specified"


class TestDigestSubjectRendering:
    """Tests for digest subject line rendering with company name."""

    def test_context_includes_company_name_single(self, sample_candidate_match):
        """Test context includes company_name for single company."""
        builder = DigestBuilder()
        context = builder.build_digest_context([sample_candidate_match], "scan-123")

        assert "company_name" in context
        assert context["company_name"] == "Test Company"

    def test_context_includes_company_name_from_source(self, sample_candidate_match):
        """Test context uses source_name when provided."""
        builder = DigestBuilder()
        context = builder.build_digest_context(
            [sample_candidate_match], "scan-123", source_name="GitHub Careers"
        )

        assert context["company_name"] == "GitHub Careers"

    def test_context_company_name_multiple_companies(self, sample_job):
        """Test context shows 'Multiple Companies' for multiple companies."""
        builder = DigestBuilder()

        # Create matches from different companies
        matches = []
        for company in ["Company A", "Company B"]:
            job = sample_job.model_copy()
            job.company = company
            job.job_key = f"job-{company}"
            candidate = create_candidate_match(job)
            matches.append(candidate)

        context = builder.build_digest_context(matches, "scan-123")

        assert context["company_name"] == "Multiple Companies"

    def test_context_company_name_prefers_source_over_multiple(self, sample_job):
        """Test source_name takes precedence even with multiple companies."""
        builder = DigestBuilder()

        # Create matches from different companies
        matches = []
        for company in ["Company A", "Company B"]:
            job = sample_job.model_copy()
            job.company = company
            job.job_key = f"job-{company}"
            candidate = create_candidate_match(job)
            matches.append(candidate)

        context = builder.build_digest_context(
            matches, "scan-123", source_name="All Greenhouse Companies"
        )

        assert context["company_name"] == "All Greenhouse Companies"

    def test_context_empty_uses_source_name(self):
        """Test empty context uses source_name if provided."""
        builder = DigestBuilder()
        context = builder.build_digest_context([], "scan-123", source_name="Test Source")

        assert context["company_name"] == "Test Source"

    def test_context_empty_without_source_name(self):
        """Test empty context defaults to 'No Companies'."""
        builder = DigestBuilder()
        context = builder.build_digest_context([], "scan-123")

        assert context["company_name"] == "No Companies"

    def test_determine_company_name_single(self, sample_job):
        """Test _determine_company_name with single company."""
        builder = DigestBuilder()

        job = sample_job.model_copy()
        job.company = "Awesome Corp"
        candidate = create_candidate_match(job)

        groups = builder.group_by_company([candidate])
        sorted_companies = list(groups.values())

        name = builder._determine_company_name(groups, sorted_companies, None)

        assert name == "Awesome Corp"

    def test_determine_company_name_source_precedence(self, sample_job):
        """Test _determine_company_name prefers source_name."""
        builder = DigestBuilder()

        candidate = create_candidate_match(sample_job)
        groups = builder.group_by_company([candidate])
        sorted_companies = list(groups.values())

        name = builder._determine_company_name(groups, sorted_companies, "Source Name")

        assert name == "Source Name"


def _scored_candidate(sample_job, key, title, score, posted_at, profile="python"):
    job = sample_job.model_copy(
        update={"job_key": key, "title": title, "external_id": key, "posted_at": posted_at}
    )
    result = MatchResult(
        is_match=True,
        matched_required_terms={"python"},
        summary="Test match",
        primary_profile=profile,
        matched_profiles=[profile],
        affinity_score=score,
    )
    return create_candidate_match(job, match_result=result)


class TestDigestAffinityRanking:
    """Digest ordering and rendering of multi-track affinity metadata."""

    def test_digest_sorts_jobs_by_affinity_score_descending(self, sample_job):
        now = datetime(2025, 11, 10, 12, 0, tzinfo=timezone.utc)
        recent_low = _scored_candidate(
            sample_job, "low", "Low Score", 2.0, now - timedelta(days=1)
        )
        older_high = _scored_candidate(
            sample_job, "high", "High Score", 8.5, now - timedelta(days=3)
        )

        context = DigestBuilder().build_digest_context([recent_low, older_high], "scan-1")

        titles = [j["title"] for j in context["companies"][0]["jobs"]]
        assert titles == ["High Score", "Low Score"]

    def test_equal_scores_tie_break_on_posted_at_and_handle_missing_dates(self, sample_job):
        now = datetime(2025, 11, 10, 12, 0, tzinfo=timezone.utc)
        older = _scored_candidate(sample_job, "old", "Older", 3.0, now - timedelta(days=2))
        newer = _scored_candidate(sample_job, "new", "Newer", 3.0, now)
        undated = _scored_candidate(sample_job, "und", "Undated", 3.0, None)

        context = DigestBuilder().build_digest_context([undated, older, newer], "scan-1")

        titles = [j["title"] for j in context["companies"][0]["jobs"]]
        assert titles == ["Newer", "Older", "Undated"]

    def test_digest_includes_profile_metadata_in_context(self, sample_job):
        candidate = _scored_candidate(sample_job, "k", "T", 4.5, sample_job.posted_at)
        candidate.match_result.matched_profiles = ["python", "rails"]

        context = DigestBuilder().build_digest_context([candidate], "scan-1")

        job_context = context["companies"][0]["jobs"][0]
        assert job_context["primary_profile"] == "python"
        assert job_context["matched_profiles"] == ["python", "rails"]
        assert job_context["affinity_score"] == 4.5

    def test_legacy_match_result_defaults_preserve_date_ordering(self, sample_job):
        newer = create_candidate_match(
            sample_job.model_copy(
                update={"job_key": "n", "title": "Newer", "posted_at": sample_job.posted_at + timedelta(days=1)}
            )
        )
        older = create_candidate_match(sample_job.model_copy(update={"job_key": "o", "title": "Older"}))

        context = DigestBuilder().build_digest_context([older, newer], "scan-1")

        jobs = context["companies"][0]["jobs"]
        assert [j["title"] for j in jobs] == ["Newer", "Older"]
        assert jobs[0]["primary_profile"] is None
        assert jobs[0]["affinity_score"] == 0.0

    def test_digest_template_renders_track_badge_when_present(self, sample_job):
        from app.notifications.templates import TemplateRenderer

        builder = DigestBuilder()
        templates = TemplateRenderer()

        named = _scored_candidate(sample_job, "k1", "Tracked Job", 7.5, sample_job.posted_at, "backend_python")
        rendered = templates.render_digest(builder.build_digest_context([named], "scan-1"))
        assert "backend_python (7.5)" in rendered["html_body"]
        assert "Track: backend_python (Score: 7.5)" in rendered["text_body"]

        default = _scored_candidate(sample_job, "k2", "Default Job", 1.0, sample_job.posted_at, "default")
        rendered = templates.render_digest(builder.build_digest_context([default], "scan-1"))
        assert "default (1.0)" not in rendered["html_body"]
        assert "Track:" not in rendered["text_body"]

        legacy = create_candidate_match(sample_job)
        rendered = templates.render_digest(builder.build_digest_context([legacy], "scan-1"))
        assert "Track:" not in rendered["text_body"]
