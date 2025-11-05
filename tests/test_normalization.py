"""Unit tests for normalization layer.

Tests the JobNormalizer service and data models for:
- Job key and content hash generation
- Timestamp handling and inheritance
- Change detection (is_new, content_changed)
- MatchableText normalization
- Batch processing with error handling
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.config.models import SourceConfig
from app.domain.models import Job, RawJob
from app.normalization import JobNormalizer, MatchableText, NormalizationContext, NormalizationResult
from app.persistence.repositories import JobRepository
from app.utils.timestamps import utc_now


@pytest.fixture
def source_config():
    """Create a test source config."""
    return SourceConfig(
        name="Example Corp",
        type="greenhouse",
        identifier="examplecorp",
        enabled=True,
    )


@pytest.fixture
def raw_job():
    """Create a test RawJob."""
    return RawJob(
        external_id="12345",
        title="Senior Software Engineer",
        company="Example Corp",
        location="Remote",
        description="We are looking for a talented engineer with 5+ years of experience.",
        url="https://boards.greenhouse.io/examplecorp/jobs/12345",
        posted_at=datetime(2025, 11, 1, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2025, 11, 2, 14, 30, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def mock_job_repo():
    """Create a mock JobRepository."""
    return MagicMock(spec=JobRepository)


@pytest.fixture
def normalizer(mock_job_repo):
    """Create a JobNormalizer instance."""
    scan_time = datetime(2025, 11, 3, 10, 0, 0, tzinfo=timezone.utc)
    return JobNormalizer(mock_job_repo, scan_timestamp=scan_time)


class TestNormalizationModels:
    """Tests for NormalizationContext, MatchableText, and NormalizationResult."""

    def test_normalization_context_creation(self, source_config):
        """Test creating NormalizationContext."""
        scan_time = utc_now()
        ctx = NormalizationContext(source_config=source_config, scan_timestamp=scan_time)

        assert ctx.source_config == source_config
        assert ctx.scan_timestamp == scan_time
        assert ctx.existing_job is None

    def test_normalization_context_with_existing_job(self, source_config, raw_job):
        """Test NormalizationContext with existing job."""
        scan_time = utc_now()
        existing = Job(
            job_key="existing_key",
            source_type="greenhouse",
            source_identifier="examplecorp",
            external_id="12345",
            title="Old Title",
            company="Example Corp",
            location="Remote",
            description="Old description",
            url="https://example.com",
            posted_at=None,
            updated_at=None,
            first_seen_at=datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
            last_seen_at=datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
            content_hash="old_hash",
        )

        ctx = NormalizationContext(
            source_config=source_config, scan_timestamp=scan_time, existing_job=existing
        )

        assert ctx.existing_job == existing

    def test_matchable_text_from_job(self, raw_job, source_config, normalizer):
        """Test creating MatchableText from a Job."""
        job = Job(
            job_key="test_key",
            source_type=source_config.type,
            source_identifier=source_config.identifier,
            external_id=raw_job.external_id,
            title=raw_job.title,
            company=raw_job.company,
            location=raw_job.location,
            description=raw_job.description,
            url=raw_job.url,
            posted_at=raw_job.posted_at,
            updated_at=raw_job.updated_at,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="test_hash",
        )

        mt = MatchableText.from_job(job)

        # Verify originals are preserved
        assert mt.title_original == raw_job.title
        assert mt.description_original == raw_job.description
        assert mt.location_original == raw_job.location

        # Verify normalized versions are lowercase and punctuation is removed
        assert "senior software engineer" in mt.title_normalized
        assert "looking for" in mt.description_normalized
        assert mt.location_normalized == mt.location_original.lower()

        # Verify full_text includes all fields
        assert "senior software engineer" in mt.full_text_normalized
        assert "remote" in mt.full_text_normalized

    def test_matchable_text_with_none_location(self):
        """Test MatchableText when location is None."""
        job = Job(
            job_key="test_key",
            source_type="greenhouse",
            source_identifier="test",
            external_id="123",
            title="Engineer",
            company="Corp",
            location=None,
            description="Description",
            url="https://example.com",
            posted_at=None,
            updated_at=None,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="hash",
        )

        mt = MatchableText.from_job(job)

        assert mt.location_original == ""
        assert mt.location_normalized == ""
        assert "engineer" in mt.full_text_normalized
        assert "description" in mt.full_text_normalized

    def test_normalization_result_should_upsert_new_job(self, raw_job):
        """Test should_upsert is True for new jobs."""
        job = Job(
            job_key="test_key",
            source_type="greenhouse",
            source_identifier="test",
            external_id=raw_job.external_id,
            title=raw_job.title,
            company=raw_job.company,
            location=raw_job.location,
            description=raw_job.description,
            url=raw_job.url,
            posted_at=raw_job.posted_at,
            updated_at=raw_job.updated_at,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="hash",
        )

        mt = MatchableText.from_job(job)
        result = NormalizationResult(
            job=job,
            existing_job=None,
            is_new=True,
            content_changed=False,
            matchable_text=mt,
            raw_job=raw_job,
        )

        assert result.should_upsert is True
        assert result.should_re_match is False

    def test_normalization_result_should_upsert_changed_content(self, raw_job):
        """Test should_upsert is True when content changed."""
        job = Job(
            job_key="test_key",
            source_type="greenhouse",
            source_identifier="test",
            external_id=raw_job.external_id,
            title=raw_job.title,
            company=raw_job.company,
            location=raw_job.location,
            description=raw_job.description,
            url=raw_job.url,
            posted_at=raw_job.posted_at,
            updated_at=raw_job.updated_at,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash="new_hash",
        )

        existing_job = Job(
            job_key="test_key",
            source_type="greenhouse",
            source_identifier="test",
            external_id=raw_job.external_id,
            title="Old Title",
            company=raw_job.company,
            location=raw_job.location,
            description="Old description",
            url=raw_job.url,
            posted_at=raw_job.posted_at,
            updated_at=raw_job.updated_at,
            first_seen_at=datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
            last_seen_at=datetime(2025, 10, 2, 0, 0, 0, tzinfo=timezone.utc),
            content_hash="old_hash",
        )

        mt = MatchableText.from_job(job)
        result = NormalizationResult(
            job=job,
            existing_job=existing_job,
            is_new=False,
            content_changed=True,
            matchable_text=mt,
            raw_job=raw_job,
        )

        assert result.should_upsert is True
        assert result.should_re_match is True


class TestJobNormalizer:
    """Tests for the JobNormalizer service."""

    def test_normalize_new_job(self, normalizer, raw_job, source_config, mock_job_repo):
        """Test normalizing a completely new job."""
        mock_job_repo.get_by_key.return_value = None

        result = normalizer.normalize(raw_job, source_config)

        assert result.is_new is True
        assert result.content_changed is True
        assert result.should_upsert is True
        assert result.existing_job is None
        assert result.job.source_type == source_config.type
        assert result.job.source_identifier == source_config.identifier
        assert result.job.external_id == raw_job.external_id
        assert result.matchable_text.title_original == raw_job.title

        # Verify job_repo was called
        mock_job_repo.get_by_key.assert_called_once()

    def test_normalize_existing_job_unchanged(self, normalizer, raw_job, source_config, mock_job_repo):
        """Test normalizing a job that hasn't changed."""
        # Create an existing job with same content
        existing_job = Job(
            job_key="test_key",
            source_type=source_config.type,
            source_identifier=source_config.identifier,
            external_id=raw_job.external_id,
            title=raw_job.title,
            company=raw_job.company,
            location=raw_job.location,
            description=raw_job.description,
            url=raw_job.url,
            posted_at=raw_job.posted_at,
            updated_at=raw_job.updated_at,
            first_seen_at=datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
            last_seen_at=datetime(2025, 10, 2, 0, 0, 0, tzinfo=timezone.utc),
            content_hash="content_hash_value",
        )

        mock_job_repo.get_by_key.return_value = existing_job

        result = normalizer.normalize(raw_job, source_config)

        assert result.is_new is False
        assert result.existing_job == existing_job
        assert result.job.first_seen_at == existing_job.first_seen_at
        assert result.job.last_seen_at == normalizer.scan_timestamp

    def test_normalize_existing_job_content_changed(
        self, normalizer, raw_job, source_config, mock_job_repo
    ):
        """Test normalizing a job where content changed."""
        # Create an existing job with different content
        existing_job = Job(
            job_key="test_key",
            source_type=source_config.type,
            source_identifier=source_config.identifier,
            external_id=raw_job.external_id,
            title="Old Title",  # Different!
            company=raw_job.company,
            location=raw_job.location,
            description="Old description",  # Different!
            url=raw_job.url,
            posted_at=raw_job.posted_at,
            updated_at=raw_job.updated_at,
            first_seen_at=datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
            last_seen_at=datetime(2025, 10, 2, 0, 0, 0, tzinfo=timezone.utc),
            content_hash="old_hash",
        )

        mock_job_repo.get_by_key.return_value = existing_job

        result = normalizer.normalize(raw_job, source_config)

        assert result.is_new is False
        assert result.content_changed is True
        assert result.should_upsert is True
        assert result.job.first_seen_at == existing_job.first_seen_at

    def test_normalize_whitespace_trimming(self, source_config, mock_job_repo, normalizer):
        """Test that whitespace is properly trimmed and collapsed."""
        raw_job_with_spaces = RawJob(
            external_id="  123  ",
            title="  Software   Engineer  ",
            company="  Corp  ",
            location="  Remote  ",
            description="  Description  with  extra  spaces  ",
            url="https://example.com",
        )

        mock_job_repo.get_by_key.return_value = None

        result = normalizer.normalize(raw_job_with_spaces, source_config)

        assert result.job.external_id == "123"
        assert result.job.title == "Software Engineer"
        assert result.job.location == "Remote"
        assert result.job.description == "Description with extra spaces"

    def test_normalize_logs_info_for_new_job(self, raw_job, source_config, mock_job_repo, normalizer):
        """Test that normalizing new jobs is logged at INFO level."""
        mock_job_repo.get_by_key.return_value = None

        with patch.object(normalizer.logger, "info") as mock_info:
            result = normalizer.normalize(raw_job, source_config)
            mock_info.assert_called_once()
            assert "Normalized job" in mock_info.call_args[0][0]

    def test_normalize_none_location(self, raw_job, source_config, mock_job_repo, normalizer):
        """Test handling of None location."""
        raw_job.location = None
        mock_job_repo.get_by_key.return_value = None

        result = normalizer.normalize(raw_job, source_config)

        assert result.job.location is None
        assert result.matchable_text.location_original == ""
        assert result.matchable_text.location_normalized == ""

    def test_normalize_timestamps_accuracy(self, raw_job, source_config, mock_job_repo):
        """Test timestamp handling."""
        scan_time = datetime(2025, 11, 3, 10, 0, 0, tzinfo=timezone.utc)
        first_seen = datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
        existing = Job(
            job_key="test",
            source_type=source_config.type,
            source_identifier=source_config.identifier,
            external_id=raw_job.external_id,
            title=raw_job.title,
            company=raw_job.company,
            location=raw_job.location,
            description=raw_job.description,
            url=raw_job.url,
            posted_at=raw_job.posted_at,
            updated_at=raw_job.updated_at,
            first_seen_at=first_seen,
            last_seen_at=datetime(2025, 10, 2, 0, 0, 0, tzinfo=timezone.utc),
            content_hash="hash",
        )

        mock_job_repo.get_by_key.return_value = existing

        normalizer = JobNormalizer(mock_job_repo, scan_timestamp=scan_time)
        result = normalizer.normalize(raw_job, source_config)

        assert result.job.first_seen_at == first_seen  # Inherited
        assert result.job.last_seen_at == scan_time  # Updated to scan time
        assert result.job.posted_at == raw_job.posted_at
        assert result.job.updated_at == raw_job.updated_at

    def test_process_batch(self, normalizer, raw_job, source_config, mock_job_repo):
        """Test batch processing of multiple jobs."""
        mock_job_repo.get_by_key.return_value = None

        raw_jobs = [
            raw_job,
            RawJob(
                external_id="456",
                title="Product Manager",
                company="Example Corp",
                location="New York",
                description="Great opportunity",
                url="https://example.com/jobs/456",
            ),
            RawJob(
                external_id="789",
                title="Data Scientist",
                company="Example Corp",
                location="San Francisco",
                description="Data analysis role",
                url="https://example.com/jobs/789",
            ),
        ]

        pairs = [(job, source_config) for job in raw_jobs]

        results = list(normalizer.process_batch(pairs))

        assert len(results) == 3
        assert all(isinstance(r, NormalizationResult) for r in results)
        assert all(r.is_new for r in results)
        assert results[0].job.external_id == "12345"
        assert results[1].job.external_id == "456"
        assert results[2].job.external_id == "789"

    def test_process_batch_continues_on_error(self, normalizer, raw_job, source_config, mock_job_repo):
        """Test that batch processing continues despite errors."""
        # First call returns None (new job)
        # Second call raises exception
        # Third call returns None (new job)
        mock_job_repo.get_by_key.side_effect = [None, Exception("DB error"), None]

        raw_jobs = [
            raw_job,
            RawJob(
                external_id="456",
                title="Manager",
                company="Corp",
                location="Remote",
                description="Opportunity",
                url="https://example.com/456",
            ),
            RawJob(
                external_id="789",
                title="Scientist",
                company="Corp",
                location="Remote",
                description="Role",
                url="https://example.com/789",
            ),
        ]

        pairs = [(job, source_config) for job in raw_jobs]

        with patch.object(normalizer.logger, "error"):
            results = list(normalizer.process_batch(pairs))

        # Should have 2 successful results despite middle error
        assert len(results) == 2
        assert results[0].job.external_id == "12345"
        assert results[1].job.external_id == "789"
