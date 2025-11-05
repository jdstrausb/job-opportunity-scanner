"""Unit tests for notification payload building.

Tests the build_notification_context function for:
- Proper merging of base payload with additional metadata
- ISO timestamp formatting
- Version hash inclusion
- Source metadata inclusion
- Required context keys presence
"""

from datetime import datetime, timezone

import pytest

from app.domain.models import Job
from app.matching.models import CandidateMatch, MatchResult
from app.normalization.models import NormalizationResult
from app.notifications.payloads import build_notification_context
from app.utils.timestamps import utc_now


@pytest.fixture
def sample_job():
    """Sample job for testing."""
    return Job(
        job_key="test_job_key_123",
        source_type="greenhouse",
        source_identifier="techcorp",
        external_id="ext_456",
        title="Senior Python Engineer",
        company="Tech Corp",
        location="Remote",
        description="Looking for Python engineer with AWS experience. Must know Django and Flask.",
        url="https://example.com/job/456",
        posted_at=datetime(2025, 11, 1, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2025, 11, 2, 14, 0, 0, tzinfo=timezone.utc),
        first_seen_at=datetime(2025, 11, 3, 8, 0, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2025, 11, 4, 9, 0, 0, tzinfo=timezone.utc),
        content_hash="abc123def456",
    )


@pytest.fixture
def sample_match_result():
    """Sample match result for testing."""
    return MatchResult(
        is_match=True,
        matched_required_terms={"python", "remote"},
        missing_required_terms=set(),
        matched_keyword_groups=[{"senior"}],
        missing_keyword_groups=[],
        matched_exclude_terms=set(),
        matched_fields={
            "title": {"python", "senior"},
            "description": {"python", "remote"},
        },
        snippets=[
            "Looking for Python engineer with AWS experience.",
            "Must know Django and Flask.",
        ],
        summary="Matched required terms: python, remote\nMatched groups: senior",
    )


@pytest.fixture
def sample_normalization_result(sample_job):
    """Sample normalization result for testing."""
    from app.domain.models import RawJob
    from app.normalization.models import MatchableText

    raw_job = RawJob(
        external_id="ext_456",
        title=sample_job.title,
        company=sample_job.company,
        location=sample_job.location,
        description=sample_job.description,
        url=sample_job.url,
        posted_at=sample_job.posted_at,
        updated_at=sample_job.updated_at,
    )

    matchable_text = MatchableText.from_job(sample_job)

    return NormalizationResult(
        job=sample_job,
        existing_job=None,
        is_new=False,
        content_changed=True,
        matchable_text=matchable_text,
        raw_job=raw_job,
    )


@pytest.fixture
def sample_candidate(sample_normalization_result, sample_match_result):
    """Sample candidate match for testing."""
    return CandidateMatch(
        normalization_result=sample_normalization_result,
        match_result=sample_match_result,
    )


def test_build_notification_context_includes_all_required_keys(sample_candidate):
    """Test that context includes all required template keys."""
    context = build_notification_context(sample_candidate)

    # Required keys from spec
    required_keys = [
        "title",
        "company",
        "location",
        "url",
        "posted_at",
        "updated_at",
        "summary",
        "snippets",
        "snippets_highlighted",
        "match_quality",
        "search_terms",
        "match_reason",
        "first_seen_at",
        "last_seen_at",
        "source_type",
        "source_identifier",
        "job_key",
        "version_hash",
    ]

    for key in required_keys:
        assert key in context, f"Missing required key: {key}"


def test_build_notification_context_job_metadata(sample_candidate):
    """Test that job metadata is correctly included."""
    context = build_notification_context(sample_candidate)

    assert context["title"] == "Senior Python Engineer"
    assert context["company"] == "Tech Corp"
    assert context["location"] == "Remote"
    assert context["url"] == "https://example.com/job/456"
    assert context["job_key"] == "test_job_key_123"


def test_build_notification_context_version_hash(sample_candidate):
    """Test that version_hash matches job content_hash."""
    context = build_notification_context(sample_candidate)

    assert context["version_hash"] == "abc123def456"
    assert context["version_hash"] == sample_candidate.job.content_hash


def test_build_notification_context_source_metadata(sample_candidate):
    """Test that source metadata is included."""
    context = build_notification_context(sample_candidate)

    assert context["source_type"] == "greenhouse"
    assert context["source_identifier"] == "techcorp"


def test_build_notification_context_timestamps_are_iso_format(sample_candidate):
    """Test that timestamps are ISO formatted strings."""
    context = build_notification_context(sample_candidate)

    # Check ISO format with timezone
    assert context["first_seen_at"] == "2025-11-03T08:00:00+00:00"
    assert context["last_seen_at"] == "2025-11-04T09:00:00+00:00"

    # Verify they're strings
    assert isinstance(context["first_seen_at"], str)
    assert isinstance(context["last_seen_at"], str)


def test_build_notification_context_search_terms(sample_candidate):
    """Test that search_terms is present and matches matched_terms_flat."""
    context = build_notification_context(sample_candidate)

    assert "search_terms" in context
    assert "matched_terms_flat" in context
    assert context["search_terms"] == context["matched_terms_flat"]
    # Should be a list of matched terms
    assert isinstance(context["search_terms"], list)


def test_build_notification_context_match_reason(sample_candidate):
    """Test that match_reason is alias of summary."""
    context = build_notification_context(sample_candidate)

    assert context["match_reason"] == context["summary"]
    assert "Matched required terms" in context["match_reason"]


def test_build_notification_context_snippets_highlighted(sample_candidate):
    """Test that highlighted snippets are present."""
    context = build_notification_context(sample_candidate)

    assert "snippets_highlighted" in context
    assert isinstance(context["snippets_highlighted"], list)
    assert len(context["snippets_highlighted"]) > 0

    # Should contain HTML bold tags
    highlighted_text = "".join(context["snippets_highlighted"])
    assert "<b>" in highlighted_text and "</b>" in highlighted_text


def test_build_notification_context_match_quality(sample_candidate):
    """Test that match_quality is included."""
    context = build_notification_context(sample_candidate)

    assert "match_quality" in context
    # Should be one of the defined quality levels
    assert context["match_quality"] in ["perfect", "partial", "excluded", "no-match"]


def test_build_notification_context_with_none_timestamps():
    """Test context building when posted_at/updated_at are None."""
    from app.domain.models import RawJob
    from app.normalization.models import MatchableText

    job = Job(
        job_key="test_key",
        source_type="lever",
        source_identifier="company",
        external_id="123",
        title="Test Job",
        company="Test Co",
        location=None,  # Also test None location
        description="Test description",
        url="https://test.com",
        posted_at=None,
        updated_at=None,
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
        content_hash="hash123",
    )

    match_result = MatchResult(
        is_match=True,
        matched_required_terms={"test"},
        snippets=["Test description"],
        summary="Test match",
    )

    raw_job = RawJob(
        external_id="123",
        title="Test Job",
        company="Test Co",
        location=None,
        description="Test description",
        url="https://test.com",
        posted_at=None,
        updated_at=None,
    )

    matchable_text = MatchableText.from_job(job)

    norm_result = NormalizationResult(
        job=job,
        existing_job=None,
        is_new=True,
        content_changed=True,
        matchable_text=matchable_text,
        raw_job=raw_job,
    )

    candidate = CandidateMatch(
        normalization_result=norm_result, match_result=match_result
    )

    context = build_notification_context(candidate)

    # Should handle None gracefully (build_notification_payload handles this)
    assert context["posted_at"] is None
    assert context["updated_at"] is None
    assert context["location"] == "Remote"  # Default from build_notification_payload
