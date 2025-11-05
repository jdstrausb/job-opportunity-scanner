"""Integration tests for normalization and matching pipeline.

Tests the complete flow from adapter output through normalization
to matching, with actual database persistence.
"""

from datetime import datetime, timezone

import pytest

from app.config.models import SearchCriteria, SourceConfig
from app.domain.models import Job, RawJob
from app.matching import CandidateMatch, KeywordMatcher
from app.normalization import JobNormalizer
from app.persistence import init_database, close_database, get_session
from app.persistence.repositories import JobRepository


@pytest.fixture
def test_database(tmp_path):
    """Setup test database with file storage."""
    db_file = tmp_path / "test_integration.db"
    db_url = f"sqlite:///{db_file}"
    init_database(db_url)
    yield db_url
    close_database()


@pytest.fixture
def source_config():
    """Create test source configuration."""
    return SourceConfig(
        name="Test Company",
        type="greenhouse",
        identifier="testco",
        enabled=True,
    )


@pytest.fixture
def search_criteria():
    """Create test search criteria."""
    return SearchCriteria(
        required_terms=["python", "remote"],
        keyword_groups=[["senior", "lead", "principal"]],
        exclude_terms=["contract", "temporary"],
    )


class TestNormalizationToPersistence:
    """Tests for normalization and persistence flow."""

    def test_new_job_normalized_and_persisted(self, test_database, source_config):
        """Test that a new job is normalized and persisted correctly."""
        raw_job = RawJob(
            external_id="ext_123",
            title="Senior Python Engineer",
            company="Test Company",
            location="Remote",
            description="We are looking for a Senior Python Engineer with 5+ years experience.",
            url="https://boards.greenhouse.io/testco/jobs/ext_123",
            posted_at=datetime(2025, 11, 1, 12, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2025, 11, 2, 14, 30, 0, tzinfo=timezone.utc),
        )

        with get_session() as session:
            repo = JobRepository(session)
            scan_time = datetime(2025, 11, 3, 10, 0, 0, tzinfo=timezone.utc)
            normalizer = JobNormalizer(repo, scan_timestamp=scan_time)

            # Normalize
            norm_result = normalizer.normalize(raw_job, source_config)

            # Verify normalization
            assert norm_result.is_new is True
            assert norm_result.content_changed is True
            assert norm_result.should_upsert is True
            assert norm_result.job.source_type == "greenhouse"
            assert norm_result.job.source_identifier == "testco"
            assert norm_result.job.external_id == "ext_123"

            # Persist
            if norm_result.should_upsert:
                repo.upsert(norm_result.job)
                session.commit()

        # Verify persistence
        with get_session() as session:
            repo = JobRepository(session)
            persisted = repo.get_by_key(norm_result.job.job_key)

            assert persisted is not None
            assert persisted.title == "Senior Python Engineer"
            assert persisted.location == "Remote"
            assert persisted.first_seen_at == scan_time
            assert persisted.last_seen_at == scan_time

    def test_job_update_detects_content_change(self, test_database, source_config):
        """Test that job updates are detected and flagged."""
        raw_job_v1 = RawJob(
            external_id="ext_123",
            title="Senior Python Engineer",
            company="Test Company",
            location="Remote",
            description="Version 1 description",
            url="https://example.com/123",
            posted_at=datetime(2025, 11, 1, 12, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2025, 11, 2, 14, 30, 0, tzinfo=timezone.utc),
        )

        # First scan - insert job
        with get_session() as session:
            repo = JobRepository(session)
            scan_time1 = datetime(2025, 11, 3, 10, 0, 0, tzinfo=timezone.utc)
            normalizer = JobNormalizer(repo, scan_timestamp=scan_time1)

            norm_result1 = normalizer.normalize(raw_job_v1, source_config)
            assert norm_result1.is_new is True

            repo.upsert(norm_result1.job)
            session.commit()
            job_key = norm_result1.job.job_key
            first_seen = norm_result1.job.first_seen_at

        # Second scan - job unchanged
        with get_session() as session:
            repo = JobRepository(session)
            scan_time2 = datetime(2025, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
            normalizer = JobNormalizer(repo, scan_timestamp=scan_time2)

            norm_result2 = normalizer.normalize(raw_job_v1, source_config)
            assert norm_result2.is_new is False
            assert norm_result2.content_changed is False
            assert norm_result2.job.first_seen_at == first_seen

            # For unchanged jobs, update last_seen only
            repo.update_last_seen(job_key, scan_time2)
            session.commit()

        # Third scan - job content changed
        raw_job_v2 = RawJob(
            external_id="ext_123",
            title="Senior Python Engineer",
            company="Test Company",
            location="Remote",
            description="Version 2 description - completely different now!",
            url="https://example.com/123",
        )

        with get_session() as session:
            repo = JobRepository(session)
            scan_time3 = datetime(2025, 11, 5, 10, 0, 0, tzinfo=timezone.utc)
            normalizer = JobNormalizer(repo, scan_timestamp=scan_time3)

            norm_result3 = normalizer.normalize(raw_job_v2, source_config)
            assert norm_result3.is_new is False
            assert norm_result3.content_changed is True
            assert norm_result3.should_upsert is True
            assert norm_result3.job.first_seen_at == first_seen  # Preserved

            repo.upsert(norm_result3.job)
            session.commit()

        # Verify final state
        with get_session() as session:
            repo = JobRepository(session)
            final_job = repo.get_by_key(job_key)

            assert final_job.description == "Version 2 description - completely different now!"
            assert final_job.first_seen_at == first_seen
            assert final_job.last_seen_at == scan_time3


class TestNormalizationToMatching:
    """Tests for normalization to matching pipeline."""

    def test_matching_pipeline_happy_path(self, test_database, source_config, search_criteria):
        """Test complete pipeline: normalize -> match -> notify."""
        raw_jobs = [
            RawJob(
                external_id="123",
                title="Senior Python Engineer",
                company="Tech Corp",
                location="Remote",
                description="We need a Senior Python Engineer with 5+ years experience.",
                url="https://example.com/123",
            ),
            RawJob(
                external_id="456",
                title="Junior PHP Developer",
                company="Web Shop",
                location="San Francisco",
                description="Looking for PHP developer with some experience.",
                url="https://example.com/456",
            ),
            RawJob(
                external_id="789",
                title="Senior Python Contractor",
                company="Consulting Inc",
                location="Remote",
                description="We have a temporary contract role for a Python specialist.",
                url="https://example.com/789",
            ),
        ]

        candidates = []

        with get_session() as session:
            repo = JobRepository(session)
            scan_time = datetime(2025, 11, 3, 10, 0, 0, tzinfo=timezone.utc)
            normalizer = JobNormalizer(repo, scan_timestamp=scan_time)
            matcher = KeywordMatcher(search_criteria)

            for raw_job in raw_jobs:
                # Normalize
                norm_result = normalizer.normalize(raw_job, source_config)

                # Persist if new/changed
                if norm_result.should_upsert:
                    repo.upsert(norm_result.job)

                # Match
                match_result = matcher.evaluate(norm_result.job, norm_result.matchable_text)

                # Create candidate
                candidate = CandidateMatch(norm_result, match_result)
                candidates.append(candidate)

            session.commit()

        # Verify results
        assert len(candidates) == 3

        # First job: should match (senior python engineer, remote, no exclusions)
        assert candidates[0].match_result.is_match is True
        assert candidates[0].should_notify is True
        assert "python" in candidates[0].match_result.matched_required_terms
        assert "remote" in candidates[0].match_result.matched_required_terms
        assert "senior" in candidates[0].match_result.matched_keyword_groups[0]

        # Second job: should NOT match (missing python, missing remote)
        assert candidates[1].match_result.is_match is False
        assert candidates[1].should_notify is False
        assert "python" in candidates[1].match_result.missing_required_terms
        assert "remote" in candidates[1].match_result.missing_required_terms

        # Third job: matches criteria but should NOT notify (contract excluded)
        assert candidates[2].match_result.is_match is False
        assert candidates[2].should_notify is False
        assert "contract" in candidates[2].match_result.matched_exclude_terms

    def test_batch_normalization_then_matching(self, test_database, source_config):
        """Test batch normalization followed by batch matching."""
        # Use simpler criteria for this test
        criteria = SearchCriteria(
            required_terms=["python", "remote"],
            keyword_groups=[],
            exclude_terms=[],
        )

        raw_jobs = [
            RawJob(
                external_id=str(i),
                title=f"Python Engineer {i}",
                company="Tech Corp",
                location="Remote" if i % 2 == 0 else "NYC",
                description=f"Looking for Python engineer #{i}",
                url=f"https://example.com/{i}",
            )
            for i in range(5)
        ]

        with get_session() as session:
            repo = JobRepository(session)
            normalizer = JobNormalizer(repo)
            matcher = KeywordMatcher(criteria)

            # Batch normalize
            norm_results = list(
                normalizer.process_batch((job, source_config) for job in raw_jobs)
            )

            # Persist all
            for norm_result in norm_results:
                repo.upsert(norm_result.job)

            # Batch match
            candidates = []
            for norm_result in norm_results:
                match_result = matcher.evaluate(norm_result.job, norm_result.matchable_text)
                candidates.append(CandidateMatch(norm_result, match_result))

            session.commit()

        # Verify
        assert len(candidates) == 5

        # Jobs with even IDs (0, 2, 4) have Remote location
        for i in range(5):
            if i % 2 == 0:  # Remote jobs have "python" and "remote"
                assert candidates[i].match_result.is_match is True
                assert "remote" in candidates[i].match_result.matched_required_terms
                assert "python" in candidates[i].match_result.matched_required_terms
            else:  # Non-remote jobs have "python" but not "remote"
                assert candidates[i].match_result.is_match is False
                assert "remote" in candidates[i].match_result.missing_required_terms

    def test_pipeline_with_location_matching(self, test_database, source_config):
        """Test that location-only keywords work correctly."""
        criteria = SearchCriteria(
            required_terms=["remote"],
            keyword_groups=[],
            exclude_terms=[],
        )

        jobs_data = [
            ("Remote Role", "Remote", True),
            ("NYC Office", "New York, NY", False),
            ("Distributed Team", "Remote / WFH", True),
        ]

        matcher = KeywordMatcher(criteria)

        with get_session() as session:
            repo = JobRepository(session)
            normalizer = JobNormalizer(repo)

            for title, location, should_match in jobs_data:
                raw = RawJob(
                    external_id=title.lower().replace(" ", "_"),
                    title=title,
                    company="Corp",
                    location=location,
                    description="Job opportunity",
                    url="https://example.com",
                )

                norm = normalizer.normalize(raw, source_config)
                match = matcher.evaluate(norm.job, norm.matchable_text)

                assert match.is_match == should_match, f"Failed for {title} in {location}"

                if should_match:
                    assert "remote" in match.matched_required_terms
