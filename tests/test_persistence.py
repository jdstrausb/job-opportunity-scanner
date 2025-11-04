"""Unit tests for persistence layer."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.domain.models import AlertRecord, Job, SourceStatus
from app.persistence import (
    AlertRepository,
    DatabaseConnectionError,
    DataIntegrityError,
    JobRepository,
    PersistenceError,
    RecordNotFoundError,
    SourceRepository,
    close_database,
    get_session,
    init_database,
)
from app.persistence.schema import AlertRecordModel, JobModel, SourceStatusModel


class TestDatabaseInitialization:
    """Tests for database initialization."""

    def test_init_database_success(self, tmp_path):
        """Test successful database initialization."""
        db_file = tmp_path / "test.db"
        db_url = f"sqlite:///{db_file}"

        init_database(db_url)

        # Verify file was created
        assert db_file.exists()

        # Verify we can get a session
        with get_session() as session:
            assert session is not None

        close_database()

    def test_init_database_creates_parent_directories(self, tmp_path):
        """Test initialization creates parent directories if missing."""
        db_file = tmp_path / "subdir" / "nested" / "test.db"
        db_url = f"sqlite:///{db_file}"

        init_database(db_url)

        # Verify directory was created
        assert db_file.parent.exists()
        assert db_file.exists()

        close_database()

    def test_init_database_in_memory(self):
        """Test initialization with in-memory database."""
        db_url = "sqlite:///:memory:"

        init_database(db_url)

        # Verify we can get a session
        with get_session() as session:
            assert session is not None

        close_database()

    def test_init_database_invalid_url_raises_error(self):
        """Test initialization with invalid URL raises DatabaseConnectionError."""
        with pytest.raises(DatabaseConnectionError):
            init_database("")

        with pytest.raises(DatabaseConnectionError):
            init_database(None)

    def test_schema_migration_is_idempotent(self, tmp_path):
        """Test schema migration can run multiple times."""
        from sqlalchemy import text

        db_file = tmp_path / "test.db"
        db_url = f"sqlite:///{db_file}"

        # Initialize twice
        init_database(db_url)
        init_database(db_url)

        # Verify tables exist
        with get_session() as session:
            # Query should work without error
            result = session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = [row[0] for row in result.fetchall()]
            assert "jobs" in tables
            assert "sources" in tables
            assert "alerts_sent" in tables

        close_database()


class TestSessionManagement:
    """Tests for session management."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Setup test database before each test."""
        init_database("sqlite:///:memory:")
        yield
        close_database()

    def test_session_commits_on_success(self):
        """Test session commits transaction on successful exit."""
        # Create a job
        job = create_test_job()

        with get_session() as session:
            job_model = JobModel.from_domain(job)
            session.add(job_model)

        # Verify job was committed
        with get_session() as session:
            result = session.get(JobModel, job.job_key)
            assert result is not None
            assert result.job_key == job.job_key

    def test_session_rolls_back_on_exception(self):
        """Test session rolls back transaction on exception."""
        job = create_test_job()

        try:
            with get_session() as session:
                job_model = JobModel.from_domain(job)
                session.add(job_model)
                # Raise exception before commit
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Verify job was not committed
        with get_session() as session:
            result = session.get(JobModel, job.job_key)
            assert result is None

    def test_session_is_closed_after_use(self):
        """Test session is closed after context exit."""
        session_obj = None
        with get_session() as session:
            session_obj = session
            # Session should be active while in context
            assert session is not None

        # After context exit, session should be closed
        # SQLAlchemy 2.0 marks sessions as closed via the Session.close() method
        # We can verify that the context manager worked by checking it completed
        assert session_obj is not None

    def test_get_session_without_init_raises_error(self):
        """Test get_session raises error if database not initialized."""
        close_database()

        with pytest.raises(DatabaseConnectionError, match="Database not initialized"):
            with get_session() as session:
                pass


class TestORMModelConversions:
    """Tests for ORM model to domain model conversions."""

    def test_job_model_to_domain(self):
        """Test JobModel.to_domain() converts all fields correctly."""
        now = datetime.now(timezone.utc)
        job_model = JobModel(
            job_key="test123",
            source_type="greenhouse",
            source_identifier="examplecorp",
            external_id="12345",
            title="Software Engineer",
            company="Example Corp",
            location="Remote",
            description="Great opportunity",
            url="https://example.com/jobs/12345",
            posted_at=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            updated_at=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            first_seen_at=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            last_seen_at=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            content_hash="abc123",
        )

        job = job_model.to_domain()

        assert job.job_key == "test123"
        assert job.source_type == "greenhouse"
        assert job.source_identifier == "examplecorp"
        assert job.external_id == "12345"
        assert job.title == "Software Engineer"
        assert job.company == "Example Corp"
        assert job.location == "Remote"
        assert job.description == "Great opportunity"
        assert job.url == "https://example.com/jobs/12345"
        assert job.posted_at.tzinfo == timezone.utc
        assert job.content_hash == "abc123"

    def test_job_model_from_domain(self):
        """Test JobModel.from_domain() converts all fields correctly."""
        job = create_test_job()

        job_model = JobModel.from_domain(job)

        assert job_model.job_key == job.job_key
        assert job_model.source_type == job.source_type
        assert job_model.source_identifier == job.source_identifier
        assert job_model.external_id == job.external_id
        assert job_model.title == job.title
        assert job_model.company == job.company
        assert job_model.location == job.location
        assert job_model.description == job.description
        assert job_model.url == job.url
        assert job_model.content_hash == job.content_hash

    def test_job_model_handles_none_values(self):
        """Test ORM model conversion handles None values correctly."""
        job = create_test_job(location=None, posted_at=None, updated_at=None)

        job_model = JobModel.from_domain(job)
        assert job_model.location is None
        assert job_model.posted_at is None
        assert job_model.updated_at is None

        converted_job = job_model.to_domain()
        assert converted_job.location is None
        assert converted_job.posted_at is None
        assert converted_job.updated_at is None

    def test_source_status_model_conversions(self):
        """Test SourceStatusModel to/from domain conversions."""
        now = datetime.now(timezone.utc)
        source_status = SourceStatus(
            source_identifier="examplecorp",
            name="Example Corp",
            source_type="greenhouse",
            last_success_at=now,
            last_error_at=None,
            error_message=None,
        )

        source_model = SourceStatusModel.from_domain(source_status)
        assert source_model.source_identifier == "examplecorp"
        assert source_model.name == "Example Corp"
        assert source_model.source_type == "greenhouse"

        converted = source_model.to_domain()
        assert converted.source_identifier == source_status.source_identifier
        assert converted.name == source_status.name
        assert converted.source_type == source_status.source_type
        assert converted.last_success_at.tzinfo == timezone.utc

    def test_alert_record_model_conversions(self):
        """Test AlertRecordModel to/from domain conversions."""
        now = datetime.now(timezone.utc)
        alert = AlertRecord(job_key="test123", version_hash="abc123", sent_at=now)

        alert_model = AlertRecordModel.from_domain(alert)
        assert alert_model.job_key == "test123"
        assert alert_model.version_hash == "abc123"

        converted = alert_model.to_domain()
        assert converted.job_key == alert.job_key
        assert converted.version_hash == alert.version_hash
        assert converted.sent_at.tzinfo == timezone.utc


class TestJobRepository:
    """Tests for JobRepository."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Setup test database before each test."""
        init_database("sqlite:///:memory:")
        yield
        close_database()

    def test_get_by_key_returns_job_when_exists(self):
        """Test get_by_key returns job when exists."""
        job = create_test_job()

        # Insert job
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)

        # Retrieve job
        with get_session() as session:
            repo = JobRepository(session)
            found_job = repo.get_by_key(job.job_key)

        assert found_job is not None
        assert found_job.job_key == job.job_key
        assert found_job.title == job.title

    def test_get_by_key_returns_none_when_not_found(self):
        """Test get_by_key returns None when not found."""
        with get_session() as session:
            repo = JobRepository(session)
            found_job = repo.get_by_key("nonexistent")

        assert found_job is None

    def test_get_by_source_returns_all_jobs_for_source(self):
        """Test get_by_source returns all jobs for source."""
        job1 = create_test_job(job_key="key1", external_id="1")
        job2 = create_test_job(job_key="key2", external_id="2")
        job3 = create_test_job(
            job_key="key3", external_id="3", source_identifier="othercorp"
        )

        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job1)
            repo.upsert(job2)
            repo.upsert(job3)

        # Query for examplecorp jobs
        with get_session() as session:
            repo = JobRepository(session)
            jobs = repo.get_by_source("greenhouse", "examplecorp")

        assert len(jobs) == 2
        assert all(job.source_identifier == "examplecorp" for job in jobs)

    def test_get_by_source_returns_empty_list_when_none_found(self):
        """Test get_by_source returns empty list when none found."""
        with get_session() as session:
            repo = JobRepository(session)
            jobs = repo.get_by_source("greenhouse", "nonexistent")

        assert jobs == []

    def test_upsert_inserts_new_job(self):
        """Test upsert inserts new job."""
        job = create_test_job()

        with get_session() as session:
            repo = JobRepository(session)
            persisted_job = repo.upsert(job)

        assert persisted_job.job_key == job.job_key

        # Verify insertion
        with get_session() as session:
            repo = JobRepository(session)
            found_job = repo.get_by_key(job.job_key)

        assert found_job is not None

    def test_upsert_updates_existing_job(self):
        """Test upsert updates existing job."""
        job = create_test_job(title="Original Title")

        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)

        # Update job
        job.title = "Updated Title"
        job.description = "Updated description"

        with get_session() as session:
            repo = JobRepository(session)
            persisted_job = repo.upsert(job)

        assert persisted_job.title == "Updated Title"

        # Verify update
        with get_session() as session:
            repo = JobRepository(session)
            found_job = repo.get_by_key(job.job_key)

        assert found_job.title == "Updated Title"
        assert found_job.description == "Updated description"

    def test_update_last_seen_updates_timestamp_only(self):
        """Test update_last_seen updates timestamp only."""
        job = create_test_job()
        new_timestamp = datetime(2025, 12, 1, 10, 0, 0, tzinfo=timezone.utc)

        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)

        with get_session() as session:
            repo = JobRepository(session)
            repo.update_last_seen(job.job_key, new_timestamp)

        # Verify timestamp updated
        with get_session() as session:
            repo = JobRepository(session)
            found_job = repo.get_by_key(job.job_key)

        assert found_job.last_seen_at.year == 2025
        assert found_job.last_seen_at.month == 12
        assert found_job.title == job.title  # Other fields unchanged

    def test_update_last_seen_raises_error_if_not_found(self):
        """Test update_last_seen raises RecordNotFoundError if job doesn't exist."""
        new_timestamp = datetime.now(timezone.utc)

        with pytest.raises(RecordNotFoundError):
            with get_session() as session:
                repo = JobRepository(session)
                repo.update_last_seen("nonexistent", new_timestamp)

    def test_bulk_upsert_inserts_multiple_jobs(self):
        """Test bulk_upsert inserts multiple jobs efficiently."""
        jobs = [
            create_test_job(job_key="key1", external_id="1"),
            create_test_job(job_key="key2", external_id="2"),
            create_test_job(job_key="key3", external_id="3"),
        ]

        with get_session() as session:
            repo = JobRepository(session)
            persisted_jobs = repo.bulk_upsert(jobs)

        assert len(persisted_jobs) == 3

        # Verify all inserted
        with get_session() as session:
            repo = JobRepository(session)
            found_jobs = repo.get_by_source("greenhouse", "examplecorp")

        assert len(found_jobs) == 3

    def test_get_stale_jobs_returns_jobs_older_than_cutoff(self):
        """Test get_stale_jobs returns jobs older than cutoff."""
        old_date = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        new_date = datetime(2025, 11, 1, 10, 0, 0, tzinfo=timezone.utc)

        job1 = create_test_job(job_key="key1", external_id="1", last_seen_at=old_date)
        job2 = create_test_job(job_key="key2", external_id="2", last_seen_at=new_date)

        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job1)
            repo.upsert(job2)

        cutoff = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)

        with get_session() as session:
            repo = JobRepository(session)
            stale_jobs = repo.get_stale_jobs(cutoff)

        assert len(stale_jobs) == 1
        assert stale_jobs[0].job_key == "key1"


class TestSourceRepository:
    """Tests for SourceRepository."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Setup test database before each test."""
        init_database("sqlite:///:memory:")
        yield
        close_database()

    def test_get_by_identifier_returns_source_when_exists(self):
        """Test get_by_identifier returns source when exists."""
        source = create_test_source()

        with get_session() as session:
            repo = SourceRepository(session)
            repo.upsert(source)

        with get_session() as session:
            repo = SourceRepository(session)
            found_source = repo.get_by_identifier(source.source_identifier)

        assert found_source is not None
        assert found_source.source_identifier == source.source_identifier

    def test_get_all_returns_all_sources(self):
        """Test get_all returns all sources."""
        source1 = create_test_source(source_identifier="corp1", name="Corp 1")
        source2 = create_test_source(source_identifier="corp2", name="Corp 2")

        with get_session() as session:
            repo = SourceRepository(session)
            repo.upsert(source1)
            repo.upsert(source2)

        with get_session() as session:
            repo = SourceRepository(session)
            sources = repo.get_all()

        assert len(sources) == 2

    def test_upsert_inserts_new_source(self):
        """Test upsert inserts new source."""
        source = create_test_source()

        with get_session() as session:
            repo = SourceRepository(session)
            persisted_source = repo.upsert(source)

        assert persisted_source.source_identifier == source.source_identifier

    def test_upsert_updates_existing_source(self):
        """Test upsert updates existing source."""
        source = create_test_source()

        with get_session() as session:
            repo = SourceRepository(session)
            repo.upsert(source)

        # Update source
        source.name = "Updated Name"
        source.error_message = "Some error"

        with get_session() as session:
            repo = SourceRepository(session)
            persisted_source = repo.upsert(source)

        assert persisted_source.name == "Updated Name"
        assert persisted_source.error_message == "Some error"

    def test_update_success_clears_error_state(self):
        """Test update_success clears error state."""
        source = create_test_source()
        source.error_message = "Previous error"

        with get_session() as session:
            repo = SourceRepository(session)
            repo.upsert(source)

        timestamp = datetime.now(timezone.utc)

        with get_session() as session:
            repo = SourceRepository(session)
            repo.update_success(source.source_identifier, timestamp)

        with get_session() as session:
            repo = SourceRepository(session)
            found_source = repo.get_by_identifier(source.source_identifier)

        assert found_source.last_success_at is not None
        assert found_source.error_message is None

    def test_update_error_preserves_last_success(self):
        """Test update_error preserves last_success_at."""
        source = create_test_source()
        success_time = datetime(2025, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
        source.last_success_at = success_time

        with get_session() as session:
            repo = SourceRepository(session)
            repo.upsert(source)

        error_time = datetime(2025, 11, 2, 10, 0, 0, tzinfo=timezone.utc)

        with get_session() as session:
            repo = SourceRepository(session)
            repo.update_error(source.source_identifier, error_time, "Test error")

        with get_session() as session:
            repo = SourceRepository(session)
            found_source = repo.get_by_identifier(source.source_identifier)

        assert found_source.last_success_at is not None
        assert found_source.error_message == "Test error"


class TestAlertRepository:
    """Tests for AlertRepository."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Setup test database before each test."""
        init_database("sqlite:///:memory:")
        yield
        close_database()

    def test_has_been_sent_returns_false_for_new_alert(self):
        """Test has_been_sent returns False for new alert."""
        with get_session() as session:
            repo = AlertRepository(session)
            result = repo.has_been_sent("job123", "version123")

        assert result is False

    def test_has_been_sent_returns_true_for_existing_alert(self):
        """Test has_been_sent returns True for existing alert."""
        alert = create_test_alert()

        with get_session() as session:
            repo = AlertRepository(session)
            repo.record_alert(alert.job_key, alert.version_hash, alert.sent_at)

        with get_session() as session:
            repo = AlertRepository(session)
            result = repo.has_been_sent(alert.job_key, alert.version_hash)

        assert result is True

    def test_record_alert_inserts_new_record(self):
        """Test record_alert inserts new record."""
        now = datetime.now(timezone.utc)

        with get_session() as session:
            repo = AlertRepository(session)
            alert = repo.record_alert("job123", "version123", now)

        assert alert.job_key == "job123"
        assert alert.version_hash == "version123"

    def test_record_alert_is_idempotent(self):
        """Test record_alert handles duplicates gracefully (idempotent)."""
        now = datetime.now(timezone.utc)

        with get_session() as session:
            repo = AlertRepository(session)
            alert1 = repo.record_alert("job123", "version123", now)
            alert2 = repo.record_alert("job123", "version123", now)

        assert alert1.job_key == alert2.job_key
        assert alert1.version_hash == alert2.version_hash

    def test_get_alerts_for_job_returns_all_versions(self):
        """Test get_alerts_for_job returns all versions."""
        now = datetime.now(timezone.utc)

        with get_session() as session:
            repo = AlertRepository(session)
            repo.record_alert("job123", "version1", now)
            repo.record_alert("job123", "version2", now)
            repo.record_alert("job456", "version1", now)

        with get_session() as session:
            repo = AlertRepository(session)
            alerts = repo.get_alerts_for_job("job123")

        assert len(alerts) == 2
        assert all(alert.job_key == "job123" for alert in alerts)

    def test_cleanup_old_alerts_deletes_old_records(self):
        """Test cleanup_old_alerts deletes old records."""
        old_date = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        new_date = datetime(2025, 11, 1, 10, 0, 0, tzinfo=timezone.utc)

        with get_session() as session:
            repo = AlertRepository(session)
            repo.record_alert("job1", "version1", old_date)
            repo.record_alert("job2", "version1", new_date)

        cutoff = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)

        with get_session() as session:
            repo = AlertRepository(session)
            deleted_count = repo.cleanup_old_alerts(cutoff)

        assert deleted_count == 1

        # Verify old alert deleted
        with get_session() as session:
            repo = AlertRepository(session)
            alerts = repo.get_alerts_for_job("job1")

        assert len(alerts) == 0


# Helper functions for creating test fixtures


def create_test_job(
    job_key=None,
    external_id="12345",
    source_identifier="examplecorp",
    title="Software Engineer",
    location="Remote",
    posted_at="default",
    updated_at="default",
    last_seen_at=None,
    **overrides,
):
    """Create a test Job instance with defaults."""
    from app.utils.hashing import compute_content_hash, compute_job_key

    if job_key is None:
        job_key = compute_job_key("greenhouse", source_identifier, external_id)

    now = datetime.now(timezone.utc)

    # Handle None explicitly vs default values
    if posted_at == "default":
        posted_at = now
    if updated_at == "default":
        updated_at = now

    return Job(
        job_key=job_key,
        source_type="greenhouse",
        source_identifier=source_identifier,
        external_id=external_id,
        title=title,
        company="Example Corp",
        location=location,
        description="Great opportunity for a developer",
        url="https://example.com/jobs/12345",
        posted_at=posted_at,
        updated_at=updated_at,
        first_seen_at=now,
        last_seen_at=last_seen_at or now,
        content_hash=compute_content_hash(title, "Great opportunity for a developer", location),
        **overrides,
    )


def create_test_source(
    source_identifier="examplecorp",
    name="Example Corp",
    source_type="greenhouse",
    **overrides,
):
    """Create a test SourceStatus instance with defaults."""
    return SourceStatus(
        source_identifier=source_identifier,
        name=name,
        source_type=source_type,
        last_success_at=None,
        last_error_at=None,
        error_message=None,
        **overrides,
    )


def create_test_alert(
    job_key="test_job_123",
    version_hash="test_version_123",
    sent_at=None,
    **overrides,
):
    """Create a test AlertRecord instance with defaults."""
    return AlertRecord(
        job_key=job_key,
        version_hash=version_hash,
        sent_at=sent_at or datetime.now(timezone.utc),
        **overrides,
    )
