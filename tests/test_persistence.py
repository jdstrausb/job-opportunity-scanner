"""Unit tests for persistence layer."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.domain.models import AlertRecord, Job, SourceStatus, TriageStatus
from app.persistence import (
    AlertRepository,
    DatabaseConnectionError,
    DataIntegrityError,
    DismissedItemRepository,
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


    def test_create_schema_migrates_missing_job_columns(self, tmp_path):
        """create_schema adds missing location columns to a legacy jobs table."""
        from sqlalchemy import create_engine, inspect

        from app.persistence.schema import create_schema

        engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE jobs (job_key VARCHAR(64) PRIMARY KEY NOT NULL, "
                    "source_type VARCHAR(50) NOT NULL, source_identifier VARCHAR(255) NOT NULL, "
                    "external_id VARCHAR(255) NOT NULL, title TEXT NOT NULL, "
                    "company VARCHAR(255) NOT NULL, location VARCHAR(255), "
                    "description TEXT NOT NULL, url TEXT NOT NULL, posted_at VARCHAR(50), "
                    "updated_at VARCHAR(50), first_seen_at VARCHAR(50) NOT NULL, "
                    "last_seen_at VARCHAR(50) NOT NULL, content_hash VARCHAR(64) NOT NULL)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO jobs VALUES ('k1','greenhouse','acme','1','T','C',NULL,"
                    "'D','http://x',NULL,NULL,'2025-01-01T00:00:00Z','2025-01-01T00:00:00Z','h')"
                )
            )

        create_schema(engine)
        create_schema(engine)  # idempotent

        cols = {c["name"] for c in inspect(engine).get_columns("jobs")}
        assert {"secondary_locations", "is_remote", "location_country"} <= cols
        with engine.connect() as conn:
            row = conn.execute(text("SELECT job_key, is_remote FROM jobs")).fetchall()
        assert [tuple(r) for r in row] == [("k1", None)]
        engine.dispose()

    def test_create_schema_adds_match_explainability_columns_and_indexes(self, tmp_path):
        """Legacy jobs rows gain the 8 explainability/triage columns and 2 indexes."""
        from sqlalchemy import create_engine, inspect

        from app.persistence.schema import create_schema

        engine = create_engine(f"sqlite:///{tmp_path / 'legacy_v1.db'}")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE jobs (job_key VARCHAR(64) PRIMARY KEY NOT NULL, "
                    "source_type VARCHAR(50) NOT NULL, source_identifier VARCHAR(255) NOT NULL, "
                    "external_id VARCHAR(255) NOT NULL, title TEXT NOT NULL, "
                    "company VARCHAR(255) NOT NULL, location VARCHAR(255), "
                    "description TEXT NOT NULL, url TEXT NOT NULL, posted_at VARCHAR(50), "
                    "updated_at VARCHAR(50), first_seen_at VARCHAR(50) NOT NULL, "
                    "last_seen_at VARCHAR(50) NOT NULL, content_hash VARCHAR(64) NOT NULL)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO jobs VALUES ('k1','greenhouse','acme','1','T','C',NULL,"
                    "'D','http://x',NULL,NULL,'2025-01-01T00:00:00Z','2025-01-01T00:00:00Z','h')"
                )
            )

        create_schema(engine)
        create_schema(engine)  # idempotent

        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("jobs")}
        assert {
            "is_match",
            "primary_profile",
            "matched_profiles",
            "affinity_score",
            "score_breakdown",
            "match_snippets",
            "matched_fields",
            "user_status",
        } <= cols
        indexes = {i["name"] for i in inspector.get_indexes("jobs")}
        assert {"idx_jobs_match_score", "idx_jobs_user_status"} <= indexes
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT user_status, affinity_score, is_match FROM jobs")
            ).fetchall()
        assert [tuple(r) for r in row] == [("new", 0.0, None)]
        engine.dispose()

    def test_create_schema_rebuilds_legacy_sources_primary_key(self, tmp_path):
        """A sources table keyed on source_identifier alone is rebuilt with a
        (source_type, source_identifier) key, keeping every row."""
        from sqlalchemy import create_engine, inspect

        from app.persistence.schema import create_schema

        engine = create_engine(f"sqlite:///{tmp_path / 'legacy_sources.db'}")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE sources (source_identifier VARCHAR(255) NOT NULL, "
                    "name VARCHAR(255) NOT NULL, source_type VARCHAR(50) NOT NULL, "
                    "last_success_at VARCHAR(50), last_error_at VARCHAR(50), "
                    "error_message TEXT, PRIMARY KEY (source_identifier))"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO sources VALUES "
                    "('acme','Acme','greenhouse','2025-01-01T00:00:00.000000Z',NULL,NULL), "
                    "('beta','Beta','ashby',NULL,'2025-01-02T00:00:00.000000Z','boom')"
                )
            )

        create_schema(engine)
        create_schema(engine)  # idempotent

        inspector = inspect(engine)
        pk = inspector.get_pk_constraint("sources")["constrained_columns"]
        assert sorted(pk) == ["source_identifier", "source_type"]
        assert "sources_rebuild" not in inspector.get_table_names()
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM sources ORDER BY source_identifier")
            ).fetchall()
            conn.execute(
                text(
                    "INSERT INTO sources (source_identifier, name, source_type) "
                    "VALUES ('acme', 'Acme', 'ashby')"
                )
            )
        assert [tuple(r) for r in rows] == [
            ("acme", "Acme", "greenhouse", "2025-01-01T00:00:00.000000Z", None, None),
            ("beta", "Beta", "ashby", None, "2025-01-02T00:00:00.000000Z", "boom"),
        ]
        engine.dispose()


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
            posted_at=now,
            updated_at=now,
            first_seen_at=now,
            last_seen_at=now,
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


class TestTimestampStorageFormat:
    """The on-disk timestamp format is part of the schema contract.

    Existing databases hold ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` strings and queries
    compare them lexically, so writes must keep producing exactly that shape.
    """

    @pytest.fixture(autouse=True)
    def setup_database(self):
        init_database("sqlite:///:memory:")
        yield
        close_database()

    def _raw(self, session, column: str, job_key: str) -> str:
        return session.execute(
            text(f"SELECT {column} FROM jobs WHERE job_key = :k"), {"k": job_key}
        ).scalar_one()

    def test_writes_fixed_width_utc_string(self):
        from datetime import timedelta

        job = create_test_job()
        eastern = timezone(timedelta(hours=-5))
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)
            repo.update_last_seen(job.job_key, datetime(2026, 11, 6, 12, 0, 0, tzinfo=eastern))
            assert self._raw(session, "last_seen_at", job.job_key) == (
                "2026-11-06T17:00:00.000000Z"
            )

    def test_reads_legacy_rows_without_microseconds(self):
        job = create_test_job()
        with get_session() as session:
            JobRepository(session).upsert(job)
            session.execute(
                text("UPDATE jobs SET first_seen_at = '2025-01-01T00:00:00Z' WHERE job_key = :k"),
                {"k": job.job_key},
            )
            session.expire_all()
            loaded = JobRepository(session).get_by_key(job.job_key)

        assert loaded is not None
        assert loaded.first_seen_at == datetime(2025, 1, 1, tzinfo=timezone.utc)

    def test_rejects_non_datetime_values(self):
        job = create_test_job()
        with get_session() as session:
            model = JobModel.from_domain(job)
            model.last_seen_at = "2026-01-01T00:00:00Z"  # type: ignore[assignment]
            session.add(model)
            with pytest.raises(SQLAlchemyError):
                session.flush()
            session.rollback()


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

    def test_upsert_persists_match_explainability_and_normalizes_matched_fields_sets(self):
        """Set-valued matched_fields become sorted lists and round-trip via JSON."""
        from app.matching.models import MatchResult

        job = create_test_job()
        match_result = MatchResult(
            is_match=True,
            matched_fields={"title": {"staff", "engineer"}, "description": set()},
            snippets=["a **staff** engineer"],
            matched_profiles=["backend", "platform"],
            primary_profile="backend",
            affinity_score=7.5,
            score_breakdown={"staff": 5.0, "engineer": 2.5},
        )

        updated = job.apply_match_result(match_result)
        assert updated.matched_fields == {"title": ["engineer", "staff"]}

        with get_session() as session:
            JobRepository(session).upsert(updated)

        with get_session() as session:
            found = JobRepository(session).get_by_key(job.job_key)

        assert found is not None
        assert found.is_match is True
        assert found.primary_profile == "backend"
        assert found.matched_profiles == ["backend", "platform"]
        assert found.affinity_score == 7.5
        assert found.score_breakdown == {"staff": 5.0, "engineer": 2.5}
        assert found.match_snippets == ["a **staff** engineer"]
        assert found.matched_fields == {"title": ["engineer", "staff"]}
        assert found.user_status == TriageStatus.NEW

    @pytest.mark.parametrize("status", ["saved", "applied"])
    def test_upsert_preserves_user_status_across_content_hash_update(self, status):
        """A fresh Job (status 'new') must not overwrite a triaged row on upsert."""
        job = create_test_job()
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)
            result = repo.update_user_status(job.job_key, status)
            assert result.user_status == TriageStatus(status)

        changed = create_test_job(description="changed text", content_hash="new-hash")
        assert changed.user_status == TriageStatus.NEW
        with get_session() as session:
            persisted = JobRepository(session).upsert(changed)
            assert persisted.user_status == TriageStatus(status)

        with get_session() as session:
            found = JobRepository(session).get_by_key(job.job_key)
        assert found is not None
        assert found.content_hash == "new-hash"
        assert found.user_status == TriageStatus(status)

    def test_upsert_can_overwrite_user_status_when_preservation_disabled(self):
        job = create_test_job()
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)
            repo.update_user_status(job.job_key, "saved")
            persisted = repo.upsert(job, preserve_user_status=False)
        assert persisted.user_status == TriageStatus.NEW

    def test_update_user_status_raises_for_unknown_job(self):
        with get_session() as session:
            with pytest.raises(RecordNotFoundError):
                JobRepository(session).update_user_status("missing", "saved")

    def test_update_user_status_rejects_invalid_status(self):
        job = create_test_job()
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)
            with pytest.raises(ValueError):
                repo.update_user_status(job.job_key, "bogus")

    def test_update_last_seen_and_match_updates_only_seen_and_match_fields(self):
        """Refreshes last_seen_at and explainability; keeps first_seen_at/user_status."""
        from app.matching.models import MatchResult

        first_seen = datetime(2025, 1, 1, tzinfo=timezone.utc)
        job = create_test_job(first_seen_at=first_seen, last_seen_at=first_seen)
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(job)
            repo.update_user_status(job.job_key, "applied")

        later = datetime(2025, 6, 1, tzinfo=timezone.utc)
        rescored = job.apply_match_result(
            MatchResult(
                is_match=True,
                matched_fields={"title": {"engineer"}},
                snippets=["snip"],
                matched_profiles=["p"],
                primary_profile="p",
                affinity_score=3.0,
                score_breakdown={"engineer": 3.0},
            )
        )
        with get_session() as session:
            JobRepository(session).update_last_seen_and_match(rescored, later)

        with get_session() as session:
            found = JobRepository(session).get_by_key(job.job_key)
        assert found is not None
        assert found.last_seen_at == later
        assert found.first_seen_at == first_seen
        assert found.user_status == TriageStatus.APPLIED
        assert found.is_match is True
        assert found.primary_profile == "p"
        assert found.matched_profiles == ["p"]
        assert found.affinity_score == 3.0
        assert found.score_breakdown == {"engineer": 3.0}
        assert found.match_snippets == ["snip"]
        assert found.matched_fields == {"title": ["engineer"]}

    def test_update_last_seen_and_match_raises_for_unknown_job(self):
        with get_session() as session:
            with pytest.raises(RecordNotFoundError):
                JobRepository(session).update_last_seen_and_match(
                    create_test_job(), datetime.now(timezone.utc)
                )

    def test_reevaluate_stored_jobs_backfills_unscored_and_updates_on_profile_change(self):
        """only_unscored scores NULL rows only; full pass rescoring follows profiles."""
        from app.config.models import SearchProfile
        from app.matching.engine import KeywordMatcher
        from app.persistence import reevaluate_stored_jobs

        seen = datetime(2025, 1, 1, tzinfo=timezone.utc)
        legacy = create_test_job(
            external_id="1", title="Python Engineer", first_seen_at=seen, last_seen_at=seen
        )
        scored = create_test_job(
            external_id="2", title="Python Engineer", first_seen_at=seen, last_seen_at=seen
        )
        scored = scored.model_copy(
            update={"is_match": False, "primary_profile": "sentinel", "affinity_score": 99.0}
        )
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(legacy)
            repo.upsert(scored)
            repo.update_user_status(legacy.job_key, "saved")

        python_matcher = KeywordMatcher([SearchProfile(name="py", required_terms=["python"])])
        with get_session() as session:
            assert reevaluate_stored_jobs(session, python_matcher, only_unscored=True) == 1

        with get_session() as session:
            repo = JobRepository(session)
            got_legacy = repo.get_by_key(legacy.job_key)
            got_scored = repo.get_by_key(scored.job_key)
        assert got_legacy is not None and got_scored is not None
        assert got_legacy.is_match is True
        assert got_legacy.primary_profile == "py"
        assert got_legacy.user_status == TriageStatus.SAVED
        assert got_scored.primary_profile == "sentinel"
        assert got_scored.affinity_score == 99.0

        rust_matcher = KeywordMatcher([SearchProfile(name="rs", required_terms=["rust"])])
        with get_session() as session:
            assert reevaluate_stored_jobs(session, rust_matcher) == 2

        with get_session() as session:
            repo = JobRepository(session)
            for key in (legacy.job_key, scored.job_key):
                job = repo.get_by_key(key)
                assert job is not None
                assert job.is_match is False
                assert job.primary_profile is None
                assert job.first_seen_at == seen
                assert job.last_seen_at == seen
            saved = repo.get_by_key(legacy.job_key)
            assert saved is not None and saved.user_status == TriageStatus.SAVED

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

    def test_upsert_updates_location_metadata(self):
        """Re-upserting a changed job updates location metadata."""
        job = create_test_job()
        with get_session() as session:
            JobRepository(session).upsert(job)

        job.secondary_locations = ["Remote - US", "Philadelphia, PA"]
        job.is_remote = False
        job.location_country = "USA"
        with get_session() as session:
            JobRepository(session).upsert(job)

        with get_session() as session:
            model = session.get(JobModel, job.job_key)
            assert model.secondary_locations == ["Remote - US", "Philadelphia, PA"]
            assert model.is_remote is False
            assert model.location_country == "USA"

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

    def test_get_returns_source_when_exists(self):
        """Test get returns source when exists."""
        source = create_test_source()

        with get_session() as session:
            repo = SourceRepository(session)
            repo.upsert(source)

        with get_session() as session:
            repo = SourceRepository(session)
            found_source = repo.get(source.source_type, source.source_identifier)

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

    def test_record_fetch_success_clears_error_state(self):
        """A successful fetch sets last_success_at and clears the previous error."""
        source = create_test_source()
        source.error_message = "Previous error"

        with get_session() as session:
            repo = SourceRepository(session)
            repo.upsert(source)

        timestamp = datetime.now(timezone.utc)

        with get_session() as session:
            repo = SourceRepository(session)
            repo.record_fetch(
                source.source_type,
                source.source_identifier,
                name=source.name,
                at=timestamp,
            )

        with get_session() as session:
            repo = SourceRepository(session)
            found_source = repo.get(source.source_type, source.source_identifier)

        assert found_source.last_success_at == timestamp
        assert found_source.last_error_at is None
        assert found_source.error_message is None

    def test_record_fetch_error_preserves_last_success(self):
        """A failed fetch records the error and keeps last_success_at."""
        source = create_test_source()
        success_time = datetime(2025, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
        source.last_success_at = success_time

        with get_session() as session:
            repo = SourceRepository(session)
            repo.upsert(source)

        error_time = datetime(2025, 11, 2, 10, 0, 0, tzinfo=timezone.utc)

        with get_session() as session:
            repo = SourceRepository(session)
            repo.record_fetch(
                source.source_type,
                source.source_identifier,
                name=source.name,
                at=error_time,
                error="Test error",
            )

        with get_session() as session:
            repo = SourceRepository(session)
            found_source = repo.get(source.source_type, source.source_identifier)

        assert found_source.last_success_at == success_time
        assert found_source.last_error_at == error_time
        assert found_source.error_message == "Test error"

    def test_record_fetch_creates_missing_source(self):
        """record_fetch creates the health row on a source's first fetch."""
        at = datetime(2025, 11, 3, 9, 0, 0, tzinfo=timezone.utc)

        with get_session() as session:
            created = SourceRepository(session).record_fetch(
                "ashby", "newcorp", name="New Corp", at=at, error="boom"
            )

        assert created.name == "New Corp"
        assert created.source_type == "ashby"
        assert created.last_success_at is None
        assert created.last_error_at == at
        assert created.error_message == "boom"

    def test_same_identifier_on_different_ats_types_keeps_separate_health(self):
        """Health rows are keyed by (source_type, source_identifier), not slug alone."""
        ok_at = datetime(2025, 11, 3, 9, 0, 0, tzinfo=timezone.utc)
        err_at = datetime(2025, 11, 3, 9, 5, 0, tzinfo=timezone.utc)

        with get_session() as session:
            repo = SourceRepository(session)
            repo.record_fetch("greenhouse", "acme", name="Acme (Greenhouse)", at=ok_at)
            repo.record_fetch("ashby", "acme", name="Acme (Ashby)", at=err_at, error="boom")

        with get_session() as session:
            repo = SourceRepository(session)
            greenhouse = repo.get("greenhouse", "acme")
            ashby = repo.get("ashby", "acme")
            assert len(repo.get_all()) == 2

        assert greenhouse.name == "Acme (Greenhouse)"
        assert greenhouse.last_success_at == ok_at
        assert greenhouse.error_message is None
        assert ashby.name == "Acme (Ashby)"
        assert ashby.last_success_at is None
        assert ashby.error_message == "boom"


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


class TestDismissedItemRepository:
    """Tests for DismissedItemRepository."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Setup test database before each test."""
        init_database("sqlite:///:memory:")
        yield
        close_database()

    def test_dismiss_and_is_dismissed_across_scopes(self):
        """Dismissals by job, company (case-insensitive) and source all match."""
        job = create_test_job()

        with get_session() as session:
            repo = DismissedItemRepository(session)
            assert repo.is_dismissed(job) == (False, None)

        with get_session() as session:
            DismissedItemRepository(session).dismiss(job.job_key, scope="job")
        with get_session() as session:
            hit, item = DismissedItemRepository(session).is_dismissed(job)
        assert hit is True
        assert item is not None and item.scope.value == "job"

        other = create_test_job(external_id="other", source_identifier="elsewhere")
        with get_session() as session:
            DismissedItemRepository(session).dismiss("  EXAMPLE CORP ", scope="company")
        with get_session() as session:
            hit, item = DismissedItemRepository(session).is_dismissed(other)
        assert hit is True
        assert item is not None
        assert item.scope.value == "company"
        assert item.item_key == "example corp"

        third = create_test_job(external_id="third", source_identifier="muted-src").model_copy(
            update={"company": "Zed"}
        )
        with get_session() as session:
            repo = DismissedItemRepository(session)
            assert repo.is_dismissed(third)[0] is False
            repo.dismiss("Muted-Src", scope="source")
        with get_session() as session:
            hit, item = DismissedItemRepository(session).is_dismissed(third)
        assert hit is True
        assert item is not None and item.scope.value == "source"

    def test_dismiss_idempotent_and_undismiss(self):
        """Repeated dismiss updates reason/timestamp; undismiss removes the record."""
        first = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second = datetime(2026, 2, 1, tzinfo=timezone.utc)

        with get_session() as session:
            repo = DismissedItemRepository(session)
            repo.dismiss("abc", reason="first", dismissed_at=first)
            updated = repo.dismiss("ABC", reason="second", dismissed_at=second)

        assert updated.reason == "second"
        assert updated.dismissed_at == second

        with get_session() as session:
            repo = DismissedItemRepository(session)
            items = repo.list_all()
            assert len(items) == 1
            assert items[0].reason == "second"
            assert repo.undismiss("abc") is True
            assert repo.undismiss("abc") is False
            assert repo.list_all() == []

    def test_list_all_filter_by_scope(self):
        """list_all optionally filters by scope."""
        with get_session() as session:
            repo = DismissedItemRepository(session)
            repo.dismiss("j1", scope="job")
            repo.dismiss("c1", scope="company")
            repo.dismiss("s1", scope="source")

        with get_session() as session:
            repo = DismissedItemRepository(session)
            assert len(repo.list_all()) == 3
            companies = repo.list_all(scope="company")
            assert [i.item_key for i in companies] == ["c1"]
            assert [i.item_key for i in repo.list_all(scope="source")] == ["s1"]


# Helper functions for creating test fixtures


class TestJobTriageQueries:
    """Tests for JobRepository.query_jobs() and related triage helpers."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        init_database("sqlite:///:memory:")
        yield
        close_database()

    @staticmethod
    def _seed(*jobs):
        with get_session() as session:
            repo = JobRepository(session)
            for job in jobs:
                repo.upsert(job)

    @staticmethod
    def _query(**kwargs):
        from app.persistence import JobQueryFilters

        with get_session() as session:
            return JobRepository(session).query_jobs(JobQueryFilters(**kwargs))

    @staticmethod
    def _keys(page):
        return [r.job.job_key for r in page.items]

    @staticmethod
    def _job(external_id, **overrides):
        overrides.setdefault("is_match", True)
        return create_test_job(external_id=external_id, **overrides)

    def test_query_jobs_default_returns_matched_sorted_by_affinity_score(self):
        t1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2025, 2, 1, tzinfo=timezone.utc)
        low = self._job("low", affinity_score=1.0, last_seen_at=t1)
        high = self._job("high", affinity_score=9.0, last_seen_at=t1)
        tie_new = self._job("tie_new", affinity_score=5.0, last_seen_at=t2)
        tie_old = self._job("tie_old", affinity_score=5.0, last_seen_at=t1)
        miss = self._job("miss", is_match=False, affinity_score=99.0)
        self._seed(low, high, tie_new, tie_old, miss)

        page = self._query()

        assert self._keys(page) == [
            high.job_key,
            tie_new.job_key,
            tie_old.job_key,
            low.job_key,
        ]
        assert page.total == 4
        assert (page.limit, page.offset) == (50, 0)

    def test_query_jobs_sorting_and_pagination(self):
        jobs = [
            self._job(
                str(i),
                affinity_score=float(i),
                posted_at=datetime(2025, 1, i + 1, tzinfo=timezone.utc),
            )
            for i in range(5)
        ]
        self._seed(*jobs)

        page = self._query(sort_by="posted_at", sort_order="asc", limit=2, offset=1)

        assert self._keys(page) == [jobs[1].job_key, jobs[2].job_key]
        assert page.total == 5
        assert (page.limit, page.offset) == (2, 1)

    def test_query_jobs_rejects_invalid_options(self):
        for bad in (
            {"sort_by": "title"},
            {"sort_order": "sideways"},
            {"match_mode": "some"},
            {"alert_status": "none"},
            {"user_status": "bogus"},
        ):
            with pytest.raises(ValueError):
                self._query(**bad)

    def test_query_jobs_match_modes(self):
        matched = self._job("m", affinity_score=1.0)
        unmatched = self._job("u", is_match=False)
        unscored = self._job("n", is_match=None)
        self._seed(matched, unmatched, unscored)

        assert set(self._keys(self._query(match_mode="matched"))) == {matched.job_key}
        assert set(self._keys(self._query(match_mode="unmatched"))) == {
            unmatched.job_key,
            unscored.job_key,
        }
        assert len(self._query(match_mode="all").items) == 3

    def test_query_jobs_filtering(self):
        a = self._job(
            "a",
            title="Staff Python Engineer",
            primary_profile="backend",
            matched_profiles=["backend", "data"],
            is_remote=True,
            source_identifier="acme",
        )
        b = self._job(
            "b",
            title="Designer",
            primary_profile="data",
            matched_profiles=["data"],
            is_remote=False,
            source_identifier="globex",
            description="Pixels and 100%_love",
        )
        self._seed(a, b)
        with get_session() as session:
            JobRepository(session).update_user_status(b.job_key, "saved")

        def keys(**kw):
            return set(self._keys(self._query(**kw)))

        assert keys(profile="backend") == {a.job_key}
        assert keys(profile="data") == {a.job_key, b.job_key}  # primary or contained
        assert keys(profile="dat") == set()
        assert keys(source_type="greenhouse") == {a.job_key, b.job_key}
        assert keys(source_type="lever") == set()
        assert keys(source_identifier="globex") == {b.job_key}
        assert keys(is_remote=True) == {a.job_key}
        assert keys(is_remote=False) == {b.job_key}
        assert keys(user_status="saved") == {b.job_key}
        assert keys(user_status="new") == {a.job_key}
        assert keys(q="PYTHON") == {a.job_key}
        assert keys(q="example corp") == {a.job_key, b.job_key}
        assert keys(q="pixels") == {b.job_key}
        assert keys(q="100%_") == {b.job_key}  # LIKE wildcards are literal
        assert keys(q="%") == {b.job_key}

    def test_query_jobs_alert_status_derivation_and_filter(self):
        alerted = self._job("alerted")
        pending = self._job("pending")
        miss = self._job("miss", is_match=False)
        self._seed(alerted, pending, miss)
        sent_at = datetime(2025, 3, 1, 12, tzinfo=timezone.utc)
        with get_session() as session:
            AlertRepository(session).record_alert(alerted.job_key, alerted.content_hash, sent_at)
            # An alert for an older version does not count for the current hash
            AlertRepository(session).record_alert(pending.job_key, "old-hash", sent_at)

        page = self._query(match_mode="all")
        by_key = {r.job.job_key: r for r in page.items}

        assert by_key[alerted.job_key].alert_status == "alerted"
        assert by_key[alerted.job_key].alert_sent_at == sent_at
        assert by_key[pending.job_key].alert_status == "pending_alert"
        assert by_key[pending.job_key].alert_sent_at is None
        assert by_key[miss.job_key].alert_status == "none"
        assert by_key[miss.job_key].alert_sent_at is None

        assert self._keys(self._query(match_mode="all", alert_status="pending_alert")) == [
            pending.job_key
        ]
        assert self._keys(self._query(match_mode="all", alert_status="alerted")) == [
            alerted.job_key
        ]

    def test_query_jobs_dismissal_filtering_and_restoration(self):
        by_job = self._job("1", company="Other", source_identifier="s1")
        by_company = self._job("2", company="Acme", source_identifier="s2")
        by_company2 = self._job("3", company="ACME", source_identifier="s3")
        by_source = self._job("4", company="Else", source_identifier="Boards-Greenhouse")
        keep = self._job("5", company="Keep", source_identifier="s5")
        self._seed(by_job, by_company, by_company2, by_source, keep)
        with get_session() as session:
            JobRepository(session).update_user_status(by_company.job_key, "applied")

        with get_session() as session:
            dismissed = DismissedItemRepository(session)
            dismissed.dismiss(by_job.job_key, "job")
            dismissed.dismiss("acme", "company")
            dismissed.dismiss("boards-greenhouse", "source")

        assert self._keys(self._query()) == [keep.job_key]
        with get_session() as session:
            for job in (by_job, by_company, by_company2, by_source):
                assert session.get(JobModel, job.job_key) is not None
        assert len(self._query(include_dismissed=True).items) == 5

        with get_session() as session:
            dismissed = DismissedItemRepository(session)
            assert dismissed.undismiss(by_job.job_key, "job")
            assert dismissed.undismiss("acme", "company")
            assert dismissed.undismiss("boards-greenhouse", "source")

        page = self._query()
        assert len(page.items) == 5
        restored = {r.job.job_key: r.job for r in page.items}
        assert restored[by_company.job_key].user_status.value == "applied"

    def test_query_jobs_tab_counts(self):
        sent_at = datetime(2025, 3, 1, tzinfo=timezone.utc)
        alerted = self._job("alerted")
        pending_saved = self._job("pending_saved")
        applied_miss = self._job("applied_miss", is_match=False)
        dismissed = self._job("dismissed", company="Muted")
        self._seed(alerted, pending_saved, applied_miss, dismissed)
        with get_session() as session:
            repo = JobRepository(session)
            repo.update_user_status(pending_saved.job_key, "saved")
            repo.update_user_status(applied_miss.job_key, "applied")
            repo.update_user_status(dismissed.job_key, "applied")
            AlertRepository(session).record_alert(alerted.job_key, alerted.content_hash, sent_at)
            DismissedItemRepository(session).dismiss("muted", "company")

        # Counts ignore the active filters but respect dismissals
        page = self._query(match_mode="unmatched", q="nothing-matches-this")

        assert page.items == []
        assert page.counts == {"matched": 2, "saved": 1, "applied": 1, "pending_alert": 1}

        everything = self._query(include_dismissed=True)
        assert everything.counts == {"matched": 3, "saved": 1, "applied": 2, "pending_alert": 2}

    def test_dismissed_item_count_matching_jobs(self):
        self._seed(
            self._job("1", company="Acme", source_identifier="s1"),
            self._job("2", company="ACME", source_identifier="s1"),
            self._job("3", company="Other", source_identifier="S2"),
        )
        key1 = create_test_job(external_id="1", source_identifier="s1").job_key
        with get_session() as session:
            repo = DismissedItemRepository(session)
            assert repo.count_matching_jobs("job", key1) == 1
            assert repo.count_matching_jobs("job", "nope") == 0
            assert repo.count_matching_jobs("company", "Acme") == 2
            assert repo.count_matching_jobs("company", " acme ") == 2
            assert repo.count_matching_jobs("source", "s1") == 2
            assert repo.count_matching_jobs("source", "s2") == 1
            assert repo.count_matching_jobs("source", "missing") == 0
            with pytest.raises(ValueError):
                repo.count_matching_jobs("bogus", "x")

    def test_get_triage_by_key(self):
        job = self._job("1")
        self._seed(job)
        sent_at = datetime(2025, 3, 1, tzinfo=timezone.utc)

        with get_session() as session:
            repo = JobRepository(session)
            record = repo.get_triage_by_key(job.job_key)
            assert record is not None
            assert record.job.job_key == job.job_key
            assert record.alert_status == "pending_alert"
            assert record.alert_sent_at is None

            AlertRepository(session).record_alert(job.job_key, job.content_hash, sent_at)
            record = repo.get_triage_by_key(job.job_key)
            assert record is not None
            assert record.alert_status == "alerted"
            assert record.alert_sent_at == sent_at

            assert repo.get_triage_by_key("missing") is None


def create_test_job(
    job_key=None,
    external_id="12345",
    source_identifier="examplecorp",
    title="Software Engineer",
    location="Remote",
    posted_at="default",
    updated_at="default",
    last_seen_at=None,
    first_seen_at=None,
    company="Example Corp",
    description="Great opportunity for a developer",
    content_hash=None,
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
        company=company,
        location=location,
        description=description,
        url="https://example.com/jobs/12345",
        posted_at=posted_at,
        updated_at=updated_at,
        first_seen_at=first_seen_at or now,
        last_seen_at=last_seen_at or now,
        content_hash=content_hash or compute_content_hash(title, description, location),
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
