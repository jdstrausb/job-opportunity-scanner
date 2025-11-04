"""Unit tests for domain models."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.models import AlertRecord, Job, RawJob, SourceStatus


class TestRawJob:
    """Tests for RawJob model."""

    def test_valid_raw_job(self):
        """Test creating a valid RawJob."""
        raw_job = RawJob(
            external_id="12345",
            title="Software Engineer",
            company="Example Corp",
            location="Remote",
            description="Great opportunity for a developer",
            url="https://example.com/jobs/12345",
            posted_at=datetime(2025, 11, 1, 12, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2025, 11, 2, 14, 30, 0, tzinfo=timezone.utc),
        )

        assert raw_job.external_id == "12345"
        assert raw_job.title == "Software Engineer"
        assert raw_job.company == "Example Corp"
        assert raw_job.location == "Remote"
        assert raw_job.url == "https://example.com/jobs/12345"

    def test_raw_job_strips_whitespace(self):
        """Test that string fields are stripped of whitespace."""
        raw_job = RawJob(
            external_id="  12345  ",
            title="  Software Engineer  ",
            company="  Example Corp  ",
            location="  Remote  ",
            description="  Great opportunity  ",
            url="  https://example.com/jobs/12345  ",
        )

        assert raw_job.external_id == "12345"
        assert raw_job.title == "Software Engineer"
        assert raw_job.company == "Example Corp"
        assert raw_job.location == "Remote"
        assert raw_job.description == "Great opportunity"
        assert raw_job.url == "https://example.com/jobs/12345"

    def test_raw_job_empty_location_becomes_none(self):
        """Test that empty location string becomes None."""
        raw_job = RawJob(
            external_id="12345",
            title="Software Engineer",
            company="Example Corp",
            location="   ",
            description="Great opportunity",
            url="https://example.com/jobs/12345",
        )

        assert raw_job.location is None

    def test_raw_job_requires_required_fields(self):
        """Test that required fields must be present."""
        with pytest.raises(ValidationError):
            RawJob(
                # Missing external_id
                title="Software Engineer",
                company="Example Corp",
                description="Great opportunity",
                url="https://example.com/jobs/12345",
            )

    def test_raw_job_rejects_empty_required_fields(self):
        """Test that required fields cannot be empty."""
        with pytest.raises(ValidationError):
            RawJob(
                external_id="",
                title="Software Engineer",
                company="Example Corp",
                description="Great opportunity",
                url="https://example.com/jobs/12345",
            )

    def test_raw_job_converts_naive_datetime_to_utc(self):
        """Test that naive datetimes are converted to UTC."""
        raw_job = RawJob(
            external_id="12345",
            title="Software Engineer",
            company="Example Corp",
            description="Great opportunity",
            url="https://example.com/jobs/12345",
            posted_at=datetime(2025, 11, 1, 12, 0, 0),  # Naive
        )

        assert raw_job.posted_at.tzinfo == timezone.utc


class TestJob:
    """Tests for Job model."""

    def test_valid_job(self):
        """Test creating a valid Job."""
        now = datetime.now(timezone.utc)
        job = Job(
            job_key="abc123",
            source_type="greenhouse",
            source_identifier="examplecorp",
            external_id="12345",
            title="Software Engineer",
            company="Example Corp",
            location="Remote",
            description="Great opportunity",
            url="https://example.com/jobs/12345",
            posted_at=datetime(2025, 11, 1, 12, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2025, 11, 2, 14, 30, 0, tzinfo=timezone.utc),
            first_seen_at=now,
            last_seen_at=now,
            content_hash="def456",
        )

        assert job.job_key == "abc123"
        assert job.source_type == "greenhouse"
        assert job.source_identifier == "examplecorp"
        assert job.external_id == "12345"

    def test_job_validates_source_type(self):
        """Test that source_type must be valid ATS type."""
        now = datetime.now(timezone.utc)

        # Valid types should work
        for source_type in ["greenhouse", "lever", "ashby"]:
            job = Job(
                job_key="abc123",
                source_type=source_type,
                source_identifier="examplecorp",
                external_id="12345",
                title="Software Engineer",
                company="Example Corp",
                description="Great opportunity",
                url="https://example.com/jobs/12345",
                first_seen_at=now,
                last_seen_at=now,
                content_hash="def456",
            )
            assert job.source_type == source_type

        # Invalid type should fail
        with pytest.raises(ValidationError):
            Job(
                job_key="abc123",
                source_type="invalid",
                source_identifier="examplecorp",
                external_id="12345",
                title="Software Engineer",
                company="Example Corp",
                description="Great opportunity",
                url="https://example.com/jobs/12345",
                first_seen_at=now,
                last_seen_at=now,
                content_hash="def456",
            )

    def test_job_normalizes_source_type_to_lowercase(self):
        """Test that source_type is normalized to lowercase."""
        now = datetime.now(timezone.utc)
        job = Job(
            job_key="abc123",
            source_type="GREENHOUSE",
            source_identifier="examplecorp",
            external_id="12345",
            title="Software Engineer",
            company="Example Corp",
            description="Great opportunity",
            url="https://example.com/jobs/12345",
            first_seen_at=now,
            last_seen_at=now,
            content_hash="def456",
        )

        assert job.source_type == "greenhouse"

    def test_job_converts_naive_datetime_to_utc(self):
        """Test that naive datetimes are converted to UTC."""
        now = datetime.now(timezone.utc)
        job = Job(
            job_key="abc123",
            source_type="greenhouse",
            source_identifier="examplecorp",
            external_id="12345",
            title="Software Engineer",
            company="Example Corp",
            description="Great opportunity",
            url="https://example.com/jobs/12345",
            posted_at=datetime(2025, 11, 1, 12, 0, 0),  # Naive
            first_seen_at=now,
            last_seen_at=now,
            content_hash="def456",
        )

        assert job.posted_at.tzinfo == timezone.utc


class TestAlertRecord:
    """Tests for AlertRecord model."""

    def test_valid_alert_record(self):
        """Test creating a valid AlertRecord."""
        now = datetime.now(timezone.utc)
        alert = AlertRecord(
            job_key="abc123",
            version_hash="def456",
            sent_at=now,
        )

        assert alert.job_key == "abc123"
        assert alert.version_hash == "def456"
        assert alert.sent_at == now

    def test_alert_record_converts_naive_datetime_to_utc(self):
        """Test that naive datetime is converted to UTC."""
        alert = AlertRecord(
            job_key="abc123",
            version_hash="def456",
            sent_at=datetime(2025, 11, 3, 10, 30, 0),  # Naive
        )

        assert alert.sent_at.tzinfo == timezone.utc


class TestSourceStatus:
    """Tests for SourceStatus model."""

    def test_valid_source_status(self):
        """Test creating a valid SourceStatus."""
        now = datetime.now(timezone.utc)
        status = SourceStatus(
            source_identifier="examplecorp",
            name="Example Corp",
            source_type="greenhouse",
            last_success_at=now,
            last_error_at=None,
            error_message=None,
        )

        assert status.source_identifier == "examplecorp"
        assert status.name == "Example Corp"
        assert status.source_type == "greenhouse"
        assert status.last_success_at == now
        assert status.last_error_at is None
        assert status.error_message is None

    def test_source_status_validates_source_type(self):
        """Test that source_type must be valid ATS type."""
        # Valid types should work
        for source_type in ["greenhouse", "lever", "ashby"]:
            status = SourceStatus(
                source_identifier="examplecorp",
                name="Example Corp",
                source_type=source_type,
            )
            assert status.source_type == source_type

        # Invalid type should fail
        with pytest.raises(ValidationError):
            SourceStatus(
                source_identifier="examplecorp",
                name="Example Corp",
                source_type="invalid",
            )

    def test_source_status_normalizes_source_type_to_lowercase(self):
        """Test that source_type is normalized to lowercase."""
        status = SourceStatus(
            source_identifier="examplecorp",
            name="Example Corp",
            source_type="LEVER",
        )

        assert status.source_type == "lever"

    def test_source_status_with_error(self):
        """Test SourceStatus with error information."""
        now = datetime.now(timezone.utc)
        status = SourceStatus(
            source_identifier="examplecorp",
            name="Example Corp",
            source_type="greenhouse",
            last_success_at=now,
            last_error_at=now,
            error_message="Connection timeout",
        )

        assert status.error_message == "Connection timeout"
        assert status.last_error_at == now

    def test_source_status_converts_naive_datetime_to_utc(self):
        """Test that naive datetimes are converted to UTC."""
        status = SourceStatus(
            source_identifier="examplecorp",
            name="Example Corp",
            source_type="greenhouse",
            last_success_at=datetime(2025, 11, 4, 10, 0, 0),  # Naive
        )

        assert status.last_success_at.tzinfo == timezone.utc
