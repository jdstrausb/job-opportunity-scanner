"""Unit tests for the matching engine.

Tests the KeywordMatcher service for:
- Required terms matching (AND logic)
- Keyword groups matching (at least one per group)
- Exclude terms detection and failure
- Field-specific match tracking
- Snippet extraction and summary formatting
- Matched terms deduplication
"""

from datetime import datetime, timezone

import pytest

from app.config.models import SearchCriteria, SourceConfig
from app.domain.models import Job
from app.matching import (
    CandidateMatch,
    KeywordMatcher,
    MatchResult,
    build_notification_payload,
    build_rationale_dict,
    format_email_body,
)
from app.normalization import MatchableText, NormalizationResult
from app.utils.timestamps import utc_now


@pytest.fixture
def search_criteria_basic():
    """Basic search criteria with required terms and a group."""
    return SearchCriteria(
        required_terms=["python", "remote"],
        keyword_groups=[["senior", "lead", "principal"]],
        exclude_terms=["contract", "temporary"],
    )


@pytest.fixture
def search_criteria_multiple_groups():
    """Search criteria with multiple keyword groups."""
    return SearchCriteria(
        required_terms=["engineer"],
        keyword_groups=[["python", "java", "go"], ["aws", "gcp", "azure"]],
        exclude_terms=[],
    )


@pytest.fixture
def job_matching():
    """A job that should match basic criteria."""
    return Job(
        job_key="matching_job",
        source_type="greenhouse",
        source_identifier="test",
        external_id="123",
        title="Senior Python Engineer",
        company="Tech Corp",
        location="Remote",
        description="We are looking for a Senior Python Engineer with AWS experience. Must have 5+ years.",
        url="https://example.com/jobs/123",
        posted_at=None,
        updated_at=None,
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
        content_hash="hash",
    )


@pytest.fixture
def job_not_matching():
    """A job that should NOT match basic criteria (missing required term)."""
    return Job(
        job_key="non_matching_job",
        source_type="greenhouse",
        source_identifier="test",
        external_id="456",
        title="Junior C++ Developer",
        company="Game Studio",
        location="San Francisco",
        description="Looking for C++ developer. Office location required.",
        url="https://example.com/jobs/456",
        posted_at=None,
        updated_at=None,
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
        content_hash="hash2",
    )


@pytest.fixture
def job_excluded():
    """A job that matches but is excluded."""
    return Job(
        job_key="excluded_job",
        source_type="greenhouse",
        source_identifier="test",
        external_id="789",
        title="Senior Python Engineer",
        company="Consulting Firm",
        location="Remote",
        description="We need a Senior Python Engineer for temporary contract work.",
        url="https://example.com/jobs/789",
        posted_at=None,
        updated_at=None,
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
        content_hash="hash3",
    )


@pytest.fixture
def matcher(search_criteria_basic):
    """Create a KeywordMatcher instance."""
    return KeywordMatcher(search_criteria_basic)


class TestMatchResult:
    """Tests for MatchResult model."""

    def test_match_result_should_notify_success(self):
        """Test should_notify returns True for successful match."""
        result = MatchResult(
            is_match=True,
            matched_required_terms={"python", "remote"},
            missing_required_terms=set(),
            matched_keyword_groups=[{"senior"}],
            missing_keyword_groups=[],
            matched_exclude_terms=set(),
            matched_fields={"title": {"senior"}, "description": {"python", "remote"}},
            snippets=["Senior Python Engineer"],
            summary="Matched",
        )

        assert result.should_notify() is True

    def test_match_result_should_notify_fail_exclude(self):
        """Test should_notify returns False when exclude terms found."""
        result = MatchResult(
            is_match=True,  # Would be match except for exclude
            matched_required_terms={"python", "remote"},
            missing_required_terms=set(),
            matched_keyword_groups=[{"senior"}],
            missing_keyword_groups=[],
            matched_exclude_terms={"contract"},  # Found exclude term
            matched_fields={},
            snippets=[],
            summary="",
        )

        assert result.should_notify() is False

    def test_match_result_quality_perfect(self):
        """Test match_quality for perfect match."""
        result = MatchResult(
            is_match=True,
            matched_required_terms={"python"},
            missing_required_terms=set(),
            matched_keyword_groups=[{"senior"}],
            missing_keyword_groups=[],
            matched_exclude_terms=set(),
        )

        assert result.match_quality == "perfect"

    def test_match_result_quality_partial(self):
        """Test match_quality for partial match (missing group)."""
        result = MatchResult(
            is_match=False,
            matched_required_terms={"python"},
            missing_required_terms=set(),
            matched_keyword_groups=[{"senior"}, set()],
            missing_keyword_groups=[1],
            matched_exclude_terms=set(),
        )

        assert result.match_quality == "partial"

    def test_match_result_quality_no_match(self):
        """Test match_quality for no match."""
        result = MatchResult(
            is_match=False,
            matched_required_terms=set(),
            missing_required_terms={"python"},
            matched_keyword_groups=[],
            missing_keyword_groups=[],
            matched_exclude_terms=set(),
        )

        assert result.match_quality == "no-match"


class TestCandidateMatch:
    """Tests for CandidateMatch coordination structure."""

    def test_candidate_match_from_results(self, job_matching, search_criteria_basic):
        """Test creating CandidateMatch from normalization and match results."""
        from app.normalization import NormalizationResult as NormResult

        mt = MatchableText.from_job(job_matching)
        norm_result = NormResult(
            job=job_matching,
            existing_job=None,
            is_new=True,
            content_changed=True,
            matchable_text=mt,
            raw_job=None,
        )

        match_result = MatchResult(
            is_match=True,
            matched_required_terms={"python", "remote"},
            missing_required_terms=set(),
            matched_keyword_groups=[{"senior"}],
            missing_keyword_groups=[],
            matched_exclude_terms=set(),
            matched_fields={"title": {"senior", "python"}},
            snippets=[],
            summary="Matched",
        )

        candidate = CandidateMatch(norm_result, match_result)

        assert candidate.job == job_matching
        assert candidate.is_new is True
        assert candidate.should_upsert is True
        assert candidate.should_notify is True

    def test_candidate_match_no_notification_excluded(self, job_excluded, search_criteria_basic):
        """Test that excluded matches don't trigger notifications."""
        from app.normalization import NormalizationResult as NormResult

        mt = MatchableText.from_job(job_excluded)
        norm_result = NormResult(
            job=job_excluded,
            existing_job=None,
            is_new=True,
            content_changed=True,
            matchable_text=mt,
            raw_job=None,
        )

        match_result = MatchResult(
            is_match=False,  # Failed due to exclude
            matched_required_terms={"python", "remote"},
            missing_required_terms=set(),
            matched_keyword_groups=[{"senior"}],
            missing_keyword_groups=[],
            matched_exclude_terms={"contract", "temporary"},
            matched_fields={},
            snippets=[],
            summary="",
        )

        candidate = CandidateMatch(norm_result, match_result)

        assert candidate.should_upsert is True  # Still persist
        assert candidate.should_notify is False  # But don't notify


class TestKeywordMatcher:
    """Tests for the KeywordMatcher engine."""

    def test_evaluate_perfect_match(self, matcher, job_matching):
        """Test evaluating a job that perfectly matches all criteria."""
        mt = MatchableText.from_job(job_matching)
        result = matcher.evaluate(job_matching, mt)

        assert result.is_match is True
        assert "python" in result.matched_required_terms
        assert "remote" in result.matched_required_terms
        assert len(result.missing_required_terms) == 0
        assert len(result.missing_keyword_groups) == 0
        assert len(result.matched_exclude_terms) == 0
        assert result.should_notify() is True

    def test_evaluate_missing_required_term(self, search_criteria_basic, job_not_matching):
        """Test evaluation fails when required term is missing."""
        matcher = KeywordMatcher(search_criteria_basic)
        mt = MatchableText.from_job(job_not_matching)
        result = matcher.evaluate(job_not_matching, mt)

        assert result.is_match is False
        assert "python" in result.missing_required_terms
        assert "remote" in result.missing_required_terms
        assert result.should_notify() is False

    def test_evaluate_exclude_term_found(self, matcher, job_excluded):
        """Test that exclude terms cause failure even if other criteria met."""
        mt = MatchableText.from_job(job_excluded)
        result = matcher.evaluate(job_excluded, mt)

        assert result.is_match is False
        assert "contract" in result.matched_exclude_terms or "temporary" in result.matched_exclude_terms
        assert result.should_notify() is False

    def test_evaluate_field_tracking(self, matcher, job_matching):
        """Test that matched terms are tracked by field."""
        mt = MatchableText.from_job(job_matching)
        result = matcher.evaluate(job_matching, mt)

        # Senior should be in title field
        assert "senior" in result.matched_fields.get("title", set())

        # Python and remote should be in description field
        assert "python" in result.matched_fields.get("description", set())

    def test_evaluate_missing_keyword_group(self, search_criteria_multiple_groups):
        """Test that missing entire keyword group fails match."""
        # This job has engineer and python, but no cloud provider (gcp, aws, azure)
        job = Job(
            job_key="no_cloud",
            source_type="greenhouse",
            source_identifier="test",
            external_id="999",
            title="Python Engineer",
            company="No Cloud Corp",
            location="Remote",
            description="We are looking for a Python engineer with backend experience.",
            url="https://example.com/jobs/999",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="hash",
        )

        matcher = KeywordMatcher(search_criteria_multiple_groups)
        mt = MatchableText.from_job(job)
        result = matcher.evaluate(job, mt)

        assert result.is_match is False
        assert 1 in result.missing_keyword_groups  # Group 1 (cloud providers) missing

    def test_evaluate_multiple_groups_success(self, search_criteria_multiple_groups):
        """Test job that matches all required terms and all groups."""
        job = Job(
            job_key="multi_group",
            source_type="greenhouse",
            source_identifier="test",
            external_id="123",
            title="Python Engineer",
            company="Cloud Corp",
            location="Remote",
            description="We need a Python engineer with AWS and GCP experience.",
            url="https://example.com",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="hash",
        )

        matcher = KeywordMatcher(search_criteria_multiple_groups)
        mt = MatchableText.from_job(job)
        result = matcher.evaluate(job, mt)

        assert result.is_match is True
        assert "engineer" in result.matched_required_terms
        assert len(result.matched_keyword_groups[0]) > 0  # Languages group
        assert len(result.matched_keyword_groups[1]) > 0  # Clouds group

    def test_evaluate_punctuation_normalization(self):
        """Test that punctuation is handled during normalization."""
        # Search criteria are normalized during validation to lowercase
        # Job content is normalized during MatchableText creation
        # Both go through the same normalization function
        criteria = SearchCriteria(
            required_terms=["developer"],  # Simple term without punctuation
            keyword_groups=[],
            exclude_terms=[],
        )

        job = Job(
            job_key="test",
            source_type="greenhouse",
            source_identifier="test",
            external_id="123",
            title="C++ Developer",
            company="Corp",
            location="Remote",
            description="Looking for a C++ specialist",
            url="https://example.com",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="hash",
        )

        matcher = KeywordMatcher(criteria)
        mt = MatchableText.from_job(job)
        result = matcher.evaluate(job, mt)

        # "developer" should match in title
        assert result.is_match is True
        assert "developer" in result.matched_required_terms

    def test_evaluate_snippets_generated(self, matcher, job_matching):
        """Test that snippets are extracted from description."""
        mt = MatchableText.from_job(job_matching)
        result = matcher.evaluate(job_matching, mt)

        assert len(result.snippets) > 0
        # Snippets should contain matched terms
        combined_snippets = " ".join(result.snippets).lower()
        assert any(term in combined_snippets for term in ["python", "remote"])

    def test_evaluate_location_only_match(self):
        """Test job that matches location-only criteria."""
        criteria = SearchCriteria(
            required_terms=["remote"],
            keyword_groups=[],
            exclude_terms=[],
        )

        job = Job(
            job_key="test",
            source_type="greenhouse",
            source_identifier="test",
            external_id="123",
            title="Manager",
            company="Corp",
            location="Remote",
            description="Product manager role",
            url="https://example.com",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="hash",
        )

        matcher = KeywordMatcher(criteria)
        mt = MatchableText.from_job(job)
        result = matcher.evaluate(job, mt)

        assert result.is_match is True
        assert "remote" in result.matched_fields.get("location", set())

    def test_evaluate_none_location_field(self):
        """Test handling of None location in matching."""
        criteria = SearchCriteria(
            required_terms=["python"],
            keyword_groups=[],
            exclude_terms=[],
        )

        job = Job(
            job_key="test",
            source_type="greenhouse",
            source_identifier="test",
            external_id="123",
            title="Python Engineer",
            company="Corp",
            location=None,
            description="We need Python engineers",
            url="https://example.com",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="hash",
        )

        matcher = KeywordMatcher(criteria)
        mt = MatchableText.from_job(job)
        result = matcher.evaluate(job, mt)

        assert result.is_match is True
        assert result.matched_fields["location"] == set()

    def test_exclude_term_ignores_substrings(self):
        """Exclude terms should not match inside other words like 'internet'."""
        criteria = SearchCriteria(
            required_terms=["engineer"],
            keyword_groups=[],
            exclude_terms=["intern"],
        )

        job = Job(
            job_key="internet_job",
            source_type="greenhouse",
            source_identifier="test",
            external_id="321",
            title="Network Engineer",
            company="Web Scale Inc",
            location="Remote",
            description="Help build a better internet with our distributed engineering team.",
            url="https://example.com/jobs/321",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="hash4",
        )

        matcher = KeywordMatcher(criteria)
        mt = MatchableText.from_job(job)
        result = matcher.evaluate(job, mt)

        assert result.is_match is True
        assert "intern" not in result.matched_exclude_terms

    def test_exclude_term_matches_whole_word(self):
        """Exclude terms should still match when the word itself appears."""
        criteria = SearchCriteria(
            required_terms=["engineer"],
            keyword_groups=[],
            exclude_terms=["intern"],
        )

        job = Job(
            job_key="intern_job",
            source_type="greenhouse",
            source_identifier="test",
            external_id="654",
            title="Software Engineer Intern",
            company="Startup Labs",
            location="Remote",
            description="This internship is for an intern engineer joining our platform team.",
            url="https://example.com/jobs/654",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="hash5",
        )

        matcher = KeywordMatcher(criteria)
        mt = MatchableText.from_job(job)
        result = matcher.evaluate(job, mt)

        assert result.is_match is False
        assert "intern" in result.matched_exclude_terms


class TestMatchingUtils:
    """Tests for matching utility functions."""

    def test_build_notification_payload(self, job_matching, search_criteria_basic):
        """Test building notification payload."""
        mt = MatchableText.from_job(job_matching)
        match_result = MatchResult(
            is_match=True,
            matched_required_terms={"python", "remote"},
            missing_required_terms=set(),
            matched_keyword_groups=[{"senior"}],
            missing_keyword_groups=[],
            matched_exclude_terms=set(),
            matched_fields={"title": {"senior"}, "description": {"python", "remote"}},
            snippets=["Senior Python Engineer for AWS"],
            summary="Matched: python, remote; Group 1: senior",
        )

        payload = build_notification_payload(job_matching, match_result)

        assert payload["job_key"] == job_matching.job_key
        assert payload["title"] == job_matching.title
        assert payload["company"] == job_matching.company
        assert payload["location"] == "Remote"
        assert payload["url"] == job_matching.url
        assert "python" in payload["matched_terms_flat"]
        assert "senior" in payload["matched_terms_flat"]
        assert len(payload["snippets_highlighted"]) > 0

    def test_build_notification_payload_none_location(self, job_matching):
        """Test notification payload with None location."""
        job_matching.location = None
        mt = MatchableText.from_job(job_matching)

        match_result = MatchResult(
            is_match=True,
            matched_required_terms={"python"},
            missing_required_terms=set(),
            matched_keyword_groups=[],
            missing_keyword_groups=[],
            matched_exclude_terms=set(),
            matched_fields={},
            snippets=[],
            summary="",
        )

        payload = build_notification_payload(job_matching, match_result)

        # Should default to "Remote" when location is None
        assert payload["location"] == "Remote"

    def test_build_rationale_dict(self):
        """Test building lightweight rationale dict."""
        result = MatchResult(
            is_match=True,
            matched_required_terms={"python"},
            missing_required_terms=set(),
            matched_keyword_groups=[{"senior"}, set()],
            missing_keyword_groups=[1],
            matched_exclude_terms=set(),
            matched_fields={"title": {"senior"}},
            snippets=["snippet1", "snippet2"],
            summary="Matched",
        )

        rationale = build_rationale_dict(result)

        assert rationale["is_match"] is True
        assert rationale["matched_required_count"] == 1
        assert rationale["matched_group_count"] == 1
        assert rationale["excluded_found"] is False
        assert rationale["snippet_count"] == 2

    def test_format_email_body(self, job_matching, search_criteria_basic):
        """Test formatting email body from notification payload."""
        match_result = MatchResult(
            is_match=True,
            matched_required_terms={"python"},
            missing_required_terms=set(),
            matched_keyword_groups=[{"senior"}],
            missing_keyword_groups=[],
            matched_exclude_terms=set(),
            matched_fields={},
            snippets=["This is a relevant excerpt about Python"],
            summary="Matched required terms: python",
        )

        payload = build_notification_payload(job_matching, match_result)
        body = format_email_body(payload)

        assert job_matching.title in body
        assert job_matching.company in body
        assert "python" in body.lower()
        assert "https://example.com" in body

    def test_format_email_body_without_snippets(self, job_matching):
        """Test formatting email without snippets."""
        match_result = MatchResult(
            is_match=True,
            matched_required_terms={"python"},
            missing_required_terms=set(),
            matched_keyword_groups=[],
            missing_keyword_groups=[],
            matched_exclude_terms=set(),
            matched_fields={},
            snippets=[],
            summary="Matched",
        )

        payload = build_notification_payload(job_matching, match_result)
        body = format_email_body(payload, include_snippets=False)

        assert job_matching.title in body
        assert "Relevant Excerpts:" not in body
