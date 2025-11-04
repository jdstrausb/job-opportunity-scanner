"""Integration tests for persistence layer end-to-end workflows."""

from datetime import datetime, timezone

import pytest

from app.domain.models import AlertRecord, Job, SourceStatus
from app.persistence import (
    AlertRepository,
    JobRepository,
    SourceRepository,
    close_database,
    get_session,
    init_database,
)
from app.utils.hashing import compute_content_hash, compute_job_key


@pytest.fixture
def test_database(tmp_path):
    """Setup test database with file storage."""
    db_file = tmp_path / "test_integration.db"
    db_url = f"sqlite:///{db_file}"
    init_database(db_url)
    yield db_url
    close_database()


class TestEndToEndJobPersistence:
    """Test end-to-end job persistence workflows."""

    def test_create_job_upsert_retrieve_verify(self, test_database):
        """Test create job → upsert → retrieve → verify all fields match."""
        # Create job
        now = datetime.now(timezone.utc)
        job = Job(
            job_key=compute_job_key("greenhouse", "testcorp", "54321"),
            source_type="greenhouse",
            source_identifier="testcorp",
            external_id="54321",
            title="Senior Backend Engineer",
            company="Test Corp",
            location="New York, NY",
            description="We're looking for an experienced backend engineer...",
            url="https://boards.greenhouse.io/testcorp/jobs/54321",
            posted_at=now,
            updated_at=now,
            first_seen_at=now,
            last_seen_at=now,
            content_hash=compute_content_hash(
                "Senior Backend Engineer",
                "We're looking for an experienced backend engineer...",
                "New York, NY",
            ),
        )

        # Upsert job
        with get_session() as session:
            repo = JobRepository(session)
            persisted_job = repo.upsert(job)

        # Retrieve job
        with get_session() as session:
            repo = JobRepository(session)
            retrieved_job = repo.get_by_key(job.job_key)

        # Verify all fields match
        assert retrieved_job is not None
        assert retrieved_job.job_key == job.job_key
        assert retrieved_job.source_type == job.source_type
        assert retrieved_job.source_identifier == job.source_identifier
        assert retrieved_job.external_id == job.external_id
        assert retrieved_job.title == job.title
        assert retrieved_job.company == job.company
        assert retrieved_job.location == job.location
        assert retrieved_job.description == job.description
        assert retrieved_job.url == job.url
        assert retrieved_job.content_hash == job.content_hash
        assert retrieved_job.posted_at.replace(microsecond=0) == job.posted_at.replace(
            microsecond=0
        )
        assert retrieved_job.first_seen_at.replace(microsecond=0) == job.first_seen_at.replace(
            microsecond=0
        )

    def test_upsert_same_job_twice_single_record(self, test_database):
        """Test upsert same job twice → verify single record exists."""
        job = create_integration_job()

        # First upsert
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)

        # Second upsert
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)

        # Verify only one record exists
        with get_session() as session:
            repo = JobRepository(session)
            jobs = repo.get_by_source(job.source_type, job.source_identifier)

        assert len(jobs) == 1
        assert jobs[0].job_key == job.job_key

    def test_update_job_content_hash_verify_updated(self, test_database):
        """Test update job content_hash → upsert → verify updated."""
        job = create_integration_job()

        # Initial upsert
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)

        # Update content hash (simulating content change)
        job.title = "Updated Title"
        job.description = "Updated description"
        job.content_hash = compute_content_hash(
            job.title, job.description, job.location
        )

        # Upsert updated job
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)

        # Verify update
        with get_session() as session:
            repo = JobRepository(session)
            retrieved_job = repo.get_by_key(job.job_key)

        assert retrieved_job.title == "Updated Title"
        assert retrieved_job.description == "Updated description"
        assert retrieved_job.content_hash == job.content_hash

    def test_create_multiple_jobs_get_by_source_all_returned(self, test_database):
        """Test create multiple jobs → get_by_source → verify all returned."""
        jobs = [
            create_integration_job(external_id="1", title="Job 1"),
            create_integration_job(external_id="2", title="Job 2"),
            create_integration_job(external_id="3", title="Job 3"),
        ]

        # Insert all jobs
        with get_session() as session:
            repo = JobRepository(session)
            for job in jobs:
                repo.upsert(job)

        # Retrieve by source
        with get_session() as session:
            repo = JobRepository(session)
            retrieved_jobs = repo.get_by_source("greenhouse", "testcorp")

        assert len(retrieved_jobs) == 3
        assert all(job.source_identifier == "testcorp" for job in retrieved_jobs)


class TestChangeDetectionWorkflow:
    """Test change detection workflow with alerts."""

    def test_full_change_detection_workflow(self, test_database):
        """Test full change detection workflow with version hashing."""
        # Create job with initial content
        job = create_integration_job()
        content_hash_v1 = job.content_hash

        # Store job
        with get_session() as session:
            job_repo = JobRepository(session)
            job_repo.upsert(job)

        # Check has_been_sent (should be False for v1)
        with get_session() as session:
            alert_repo = AlertRepository(session)
            has_been_sent = alert_repo.has_been_sent(job.job_key, content_hash_v1)

        assert has_been_sent is False

        # Record alert for v1
        with get_session() as session:
            alert_repo = AlertRepository(session)
            alert_repo.record_alert(job.job_key, content_hash_v1, datetime.now(timezone.utc))

        # Check has_been_sent (should be True for v1)
        with get_session() as session:
            alert_repo = AlertRepository(session)
            has_been_sent = alert_repo.has_been_sent(job.job_key, content_hash_v1)

        assert has_been_sent is True

        # Update job content (simulate job description change)
        job.description = "Updated job description with new requirements"
        content_hash_v2 = compute_content_hash(job.title, job.description, job.location)
        job.content_hash = content_hash_v2

        # Upsert updated job
        with get_session() as session:
            job_repo = JobRepository(session)
            job_repo.upsert(job)

        # Check has_been_sent for v2 (should be False - new version)
        with get_session() as session:
            alert_repo = AlertRepository(session)
            has_been_sent_v2 = alert_repo.has_been_sent(job.job_key, content_hash_v2)

        assert has_been_sent_v2 is False

        # Check has_been_sent for v1 (should still be True)
        with get_session() as session:
            alert_repo = AlertRepository(session)
            has_been_sent_v1 = alert_repo.has_been_sent(job.job_key, content_hash_v1)

        assert has_been_sent_v1 is True

        # Record alert for v2
        with get_session() as session:
            alert_repo = AlertRepository(session)
            alert_repo.record_alert(job.job_key, content_hash_v2, datetime.now(timezone.utc))

        # Verify both versions tracked
        with get_session() as session:
            alert_repo = AlertRepository(session)
            alerts = alert_repo.get_alerts_for_job(job.job_key)

        assert len(alerts) == 2
        version_hashes = {alert.version_hash for alert in alerts}
        assert content_hash_v1 in version_hashes
        assert content_hash_v2 in version_hashes


class TestSourceHealthTracking:
    """Test source health tracking workflow."""

    def test_source_health_tracking_workflow(self, test_database):
        """Test initialize source → update success → update error → update success."""
        source_id = "healthcorp"
        source_name = "Health Corp"

        # Initialize source via upsert
        source = SourceStatus(
            source_identifier=source_id,
            name=source_name,
            source_type="lever",
            last_success_at=None,
            last_error_at=None,
            error_message=None,
        )

        with get_session() as session:
            repo = SourceRepository(session)
            repo.upsert(source)

        # Update with success
        success_time = datetime(2025, 11, 4, 10, 0, 0, tzinfo=timezone.utc)

        with get_session() as session:
            repo = SourceRepository(session)
            repo.update_success(source_id, success_time)

        # Verify success recorded
        with get_session() as session:
            repo = SourceRepository(session)
            source = repo.get_by_identifier(source_id)

        assert source.last_success_at is not None
        assert source.last_error_at is None
        assert source.error_message is None

        # Update with error
        error_time = datetime(2025, 11, 4, 11, 0, 0, tzinfo=timezone.utc)
        error_msg = "Connection timeout after 30 seconds"

        with get_session() as session:
            repo = SourceRepository(session)
            repo.update_error(source_id, error_time, error_msg)

        # Verify error recorded and success preserved
        with get_session() as session:
            repo = SourceRepository(session)
            source = repo.get_by_identifier(source_id)

        assert source.last_success_at is not None  # Preserved
        assert source.last_error_at is not None
        assert source.error_message == error_msg

        # Update with success again
        success_time_2 = datetime(2025, 11, 4, 12, 0, 0, tzinfo=timezone.utc)

        with get_session() as session:
            repo = SourceRepository(session)
            repo.update_success(source_id, success_time_2)

        # Verify error cleared
        with get_session() as session:
            repo = SourceRepository(session)
            source = repo.get_by_identifier(source_id)

        assert source.last_success_at.hour == 12
        assert source.last_error_at is None
        assert source.error_message is None


class TestMultiRepositoryWorkflow:
    """Test workflows using multiple repositories together."""

    def test_complete_scan_workflow(self, test_database):
        """Test a complete scan workflow: jobs + source status + alerts."""
        # Simulate a source scan
        source_id = "scancorp"
        source_name = "Scan Corp"

        # Initialize source
        source = SourceStatus(
            source_identifier=source_id,
            name=source_name,
            source_type="ashby",
            last_success_at=None,
            last_error_at=None,
            error_message=None,
        )

        with get_session() as session:
            source_repo = SourceRepository(session)
            source_repo.upsert(source)

        # Create and store jobs
        jobs = [
            create_integration_job(external_id="1", title="Job 1", source_identifier=source_id, source_type="ashby"),
            create_integration_job(external_id="2", title="Job 2", source_identifier=source_id, source_type="ashby"),
        ]

        with get_session() as session:
            job_repo = JobRepository(session)
            for job in jobs:
                job_repo.upsert(job)

        # Record alerts for matching jobs
        now = datetime.now(timezone.utc)

        with get_session() as session:
            alert_repo = AlertRepository(session)
            for job in jobs:
                alert_repo.record_alert(job.job_key, job.content_hash, now)

        # Update source with success
        with get_session() as session:
            source_repo = SourceRepository(session)
            source_repo.update_success(source_id, now)

        # Verify everything persisted correctly
        with get_session() as session:
            job_repo = JobRepository(session)
            source_repo = SourceRepository(session)
            alert_repo = AlertRepository(session)

            # Check jobs
            stored_jobs = job_repo.get_by_source("ashby", source_id)
            assert len(stored_jobs) == 2

            # Check source status
            stored_source = source_repo.get_by_identifier(source_id)
            assert stored_source.last_success_at is not None
            assert stored_source.error_message is None

            # Check alerts
            for job in jobs:
                has_alert = alert_repo.has_been_sent(job.job_key, job.content_hash)
                assert has_alert is True


# Helper functions


def create_integration_job(
    external_id="12345",
    title="Integration Test Job",
    source_identifier="testcorp",
    source_type="greenhouse",
    **overrides,
):
    """Create a job for integration testing."""
    now = datetime.now(timezone.utc)
    description = "This is a test job description for integration testing"
    location = "Remote"

    return Job(
        job_key=compute_job_key(source_type, source_identifier, external_id),
        source_type=source_type,
        source_identifier=source_identifier,
        external_id=external_id,
        title=title,
        company="Test Corp",
        location=location,
        description=description,
        url=f"https://boards.greenhouse.io/{source_identifier}/jobs/{external_id}",
        posted_at=now,
        updated_at=now,
        first_seen_at=now,
        last_seen_at=now,
        content_hash=compute_content_hash(title, description, location),
        **overrides,
    )
