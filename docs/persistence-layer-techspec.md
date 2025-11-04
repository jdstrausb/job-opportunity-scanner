# Persistence Layer — Technical Design Specification

## Context and Purpose

This document provides a detailed implementation guide for Step 4 of the Job Opportunity Scanner project: the **Persistence Layer**. This layer is responsible for managing SQLite database operations, including connection handling, schema management, and CRUD operations for jobs, sources, and alert records.

**Prerequisites:** Steps 1-3 must be completed:
- Step 1: Project scaffolding with Python package structure
- Step 2: Configuration module with YAML parsing and validation
- Step 3: Domain models (Job, RawJob, AlertRecord, SourceStatus) and utility helpers

## Integration Points

The persistence layer integrates with the following existing components:

### Domain Models (app/domain/models.py)
- `Job`: Normalized job posting with metadata (job_key, source info, timestamps, content_hash)
- `AlertRecord`: Tracks sent notifications to prevent duplicates
- `SourceStatus`: Tracks source health and error state
- `RawJob`: Not persisted directly; converted to Job first

### Configuration (app/config/)
- `EnvironmentConfig`: Contains `database_url` (default: "sqlite:///./data/job_scanner.db")
- Database URL loaded from `DATABASE_URL` environment variable or default

### Utilities (app/utils/)
- `compute_job_key()`: Generates unique job identifier from source info
- `compute_content_hash()`: Generates hash for change detection

### Project Dependencies (pyproject.toml)
- SQLAlchemy >= 2.0.0 (already declared)
- Python 3.13+ with type hints

## Database Schema Design

### Table: jobs

Stores normalized job postings with tracking metadata.

**Columns:**
- `job_key` (TEXT, PRIMARY KEY): Unique identifier hash (64-char hex from SHA256)
- `source_type` (TEXT, NOT NULL): ATS type (greenhouse, lever, ashby)
- `source_identifier` (TEXT, NOT NULL): Company identifier in the ATS
- `external_id` (TEXT, NOT NULL): Job ID from the ATS
- `title` (TEXT, NOT NULL): Job title
- `company` (TEXT, NOT NULL): Company name
- `location` (TEXT, NULL): Job location (nullable)
- `description` (TEXT, NOT NULL): Full job description text
- `url` (TEXT, NOT NULL): Direct link to job posting
- `posted_at` (TEXT, NULL): ISO 8601 timestamp when job was posted (UTC)
- `updated_at` (TEXT, NULL): ISO 8601 timestamp when job was last updated (UTC)
- `first_seen_at` (TEXT, NOT NULL): ISO 8601 timestamp when we first saw this job (UTC)
- `last_seen_at` (TEXT, NOT NULL): ISO 8601 timestamp when we last saw this job (UTC)
- `content_hash` (TEXT, NOT NULL): Hash of title + description + location (64-char hex)

**Indexes:**
- PRIMARY KEY on `job_key`
- INDEX on `source_type, source_identifier` (for querying by source)
- INDEX on `last_seen_at` (for cleanup queries)
- INDEX on `content_hash` (for change detection)

**Notes:**
- Store datetimes as ISO 8601 strings in UTC timezone
- `location` is nullable since some jobs may not specify location
- `posted_at` and `updated_at` are nullable since some ATS APIs don't provide them

### Table: sources

Tracks source health and status for observability.

**Columns:**
- `source_identifier` (TEXT, PRIMARY KEY): Company identifier (matches SourceConfig.identifier)
- `name` (TEXT, NOT NULL): Human-readable source name
- `source_type` (TEXT, NOT NULL): ATS type (greenhouse, lever, ashby)
- `last_success_at` (TEXT, NULL): ISO 8601 timestamp of last successful fetch (UTC)
- `last_error_at` (TEXT, NULL): ISO 8601 timestamp of last error (UTC)
- `error_message` (TEXT, NULL): Most recent error message

**Indexes:**
- PRIMARY KEY on `source_identifier`

**Notes:**
- One row per configured source
- Updated after each scan attempt (success or failure)
- Provides debugging visibility into source health

### Table: alerts_sent

Tracks which job versions have been alerted to prevent duplicate notifications.

**Columns:**
- `job_key` (TEXT, NOT NULL): Job identifier (foreign key to jobs.job_key)
- `version_hash` (TEXT, NOT NULL): Content hash at time of alert
- `sent_at` (TEXT, NOT NULL): ISO 8601 timestamp when alert was sent (UTC)
- PRIMARY KEY (`job_key`, `version_hash`)

**Indexes:**
- PRIMARY KEY on (`job_key`, `version_hash`)
- INDEX on `sent_at` (for cleanup/reporting queries)

**Notes:**
- Composite primary key allows one alert per job version
- When job content changes, `version_hash` changes, allowing new alert
- `version_hash` equals the Job.content_hash at time of notification

## Architecture and Module Structure

### Module Organization

```
app/persistence/
├── __init__.py          # Public API exports
├── database.py          # Connection management, session handling
├── schema.py            # SQLAlchemy ORM models and migration
├── repositories.py      # Data access layer (CRUD operations)
└── exceptions.py        # Persistence-specific exceptions
```

### Layer Responsibilities

**database.py**: Database connection and session lifecycle
- Create SQLAlchemy engine from DATABASE_URL
- Provide session factory and context manager
- Handle connection pooling and transaction management
- Expose initialization function to create schema

**schema.py**: Database schema definition and migration
- Define SQLAlchemy ORM models (JobModel, SourceStatusModel, AlertRecordModel)
- Map ORM models to domain models (Job, SourceStatus, AlertRecord)
- Provide migration function to create tables if not exist
- Include index definitions for performance

**repositories.py**: Business logic data access layer
- JobRepository: CRUD operations for jobs (upsert, get, query by source)
- SourceRepository: CRUD operations for sources (upsert, get, get all)
- AlertRepository: CRUD operations for alerts (check if sent, record alert)
- Each repository uses session context and returns domain models (not ORM models)

**exceptions.py**: Persistence-specific exceptions
- `PersistenceError`: Base exception for database errors
- `DatabaseConnectionError`: Connection/initialization failures
- `RecordNotFoundError`: Query returns no results when expected

### Data Flow

1. Application initializes database connection via `init_database(database_url)`
2. Components request sessions via `get_session()` context manager
3. Repositories perform operations within session context
4. ORM models are converted to/from domain models at repository boundary
5. Transactions commit automatically on context exit (or rollback on exception)

## Implementation Details

### Database Initialization

**Function:** `init_database(database_url: str) -> None`

**Purpose:** Initialize database connection and create schema if tables don't exist.

**Behavior:**
- Parse database_url (only SQLite supported in v1.0)
- Create SQLAlchemy engine with appropriate settings
- For SQLite: enable foreign keys, use check_same_thread=False for testing
- Call schema migration to create tables if absent
- Validate connection by executing simple query
- Raise DatabaseConnectionError if connection fails

**Error Handling:**
- Invalid database URL format: raise DatabaseConnectionError with clear message
- File path issues (directory doesn't exist): create parent directories automatically
- SQLite version compatibility: log warning if < 3.24.0

**Invocation:** Called once during application startup in main.py

### Session Management

**Function:** `get_session() -> ContextManager[Session]`

**Purpose:** Provide a database session with automatic transaction management.

**Behavior:**
- Yield SQLAlchemy Session from session factory
- Automatically commit on successful context exit
- Automatically rollback on exception
- Ensure session is closed after use

**Usage Pattern:**
```python
with get_session() as session:
    repo = JobRepository(session)
    job = repo.get_by_key("abc123")
```

**Thread Safety:**
- Sessions are not thread-safe; create new session per operation
- Engine is thread-safe and shared across application

### Schema Migration

**Function:** `create_schema(engine: Engine) -> None`

**Purpose:** Create all tables and indexes if they don't exist (idempotent).

**Behavior:**
- Use SQLAlchemy metadata.create_all() with checkfirst=True
- Creates tables in correct order (respecting foreign key dependencies)
- Creates indexes after tables
- No-op if schema already exists
- Log creation events at INFO level

**Migration Strategy (v1.0):**
- Simple "create if not exists" approach
- No support for schema changes or rollback
- Future versions may use Alembic for migrations

### ORM Model Definitions

**SQLAlchemy Models:** Map 1:1 to database tables

**JobModel:**
- Table name: "jobs"
- All columns as defined in schema
- Methods: `to_domain()` converts to Job domain model
- Constructor: `from_domain(job: Job)` creates ORM model from domain

**SourceStatusModel:**
- Table name: "sources"
- All columns as defined in schema
- Methods: `to_domain()` and `from_domain()`

**AlertRecordModel:**
- Table name: "alerts_sent"
- Composite primary key on (job_key, version_hash)
- Methods: `to_domain()` and `from_domain()`

**Datetime Handling:**
- Store as ISO 8601 strings in database
- Convert to/from Python datetime objects in ORM models
- Ensure all datetimes are UTC (validate in conversion methods)
- Use format: "YYYY-MM-DDTHH:MM:SS.ffffffZ" for consistency

### JobRepository

**Purpose:** Encapsulate all job-related database operations.

**Constructor:** `__init__(self, session: Session)`

**Methods:**

#### `get_by_key(job_key: str) -> Optional[Job]`
- Retrieve job by primary key
- Returns None if not found
- Converts ORM model to domain model

#### `get_by_source(source_type: str, source_identifier: str) -> List[Job]`
- Query all jobs for a given source
- Returns empty list if none found
- Ordered by last_seen_at DESC

#### `upsert(job: Job) -> Job`
- Insert new job or update existing job
- Use job_key as unique identifier
- Update all fields on conflict
- Return the persisted job (with any database-applied changes)
- Implementation: Check if exists, then INSERT or UPDATE

#### `update_last_seen(job_key: str, timestamp: datetime) -> None`
- Update only the last_seen_at timestamp
- Used when job hasn't changed but was seen in latest scan
- Raise RecordNotFoundError if job_key doesn't exist

#### `bulk_upsert(jobs: List[Job]) -> List[Job]`
- Efficiently upsert multiple jobs in single transaction
- Use SQLAlchemy bulk operations for performance
- Return list of persisted jobs
- Optimization: Use INSERT ... ON CONFLICT DO UPDATE (SQLite 3.24+)

#### `get_stale_jobs(cutoff: datetime) -> List[Job]`
- Find jobs not seen since cutoff timestamp
- Used for cleanup of removed postings
- Ordered by last_seen_at ASC

**Error Handling:**
- Database errors wrapped in PersistenceError
- Integrity constraint violations logged and re-raised
- All exceptions preserve original traceback

### SourceRepository

**Purpose:** Manage source status and health tracking.

**Constructor:** `__init__(self, session: Session)`

**Methods:**

#### `get_by_identifier(source_identifier: str) -> Optional[SourceStatus]`
- Retrieve source status by identifier
- Returns None if not found

#### `get_all() -> List[SourceStatus]`
- Retrieve all source status records
- Returns empty list if none exist
- Ordered by name

#### `upsert(source_status: SourceStatus) -> SourceStatus`
- Insert new source or update existing
- Use source_identifier as unique key
- Return persisted source status

#### `update_success(source_identifier: str, timestamp: datetime) -> None`
- Update last_success_at after successful scan
- Clear last_error_at and error_message
- Upsert if source doesn't exist (with minimal data)

#### `update_error(source_identifier: str, timestamp: datetime, error_message: str) -> None`
- Update last_error_at and error_message after failure
- Keep last_success_at unchanged
- Upsert if source doesn't exist

**Use Case:**
- Called after each source scan (success or failure)
- Provides observability into source health
- Enables monitoring and alerting on persistent failures

### AlertRepository

**Purpose:** Track sent alerts to prevent duplicates.

**Constructor:** `__init__(self, session: Session)`

**Methods:**

#### `has_been_sent(job_key: str, version_hash: str) -> bool`
- Check if alert already sent for this job version
- Returns True if record exists, False otherwise
- Used to prevent duplicate notifications

#### `record_alert(job_key: str, version_hash: str, sent_at: datetime) -> AlertRecord`
- Insert alert record after successful notification
- Use composite primary key (job_key, version_hash)
- Raise IntegrityError if duplicate (indicates race condition)
- Return persisted alert record

#### `get_alerts_for_job(job_key: str) -> List[AlertRecord]`
- Retrieve all alerts sent for a job (across all versions)
- Returns empty list if none sent
- Ordered by sent_at DESC

#### `cleanup_old_alerts(cutoff: datetime) -> int`
- Delete alert records older than cutoff
- Used for database maintenance
- Return count of deleted records

**Idempotency Considerations:**
- `record_alert` may be called multiple times if email send succeeds but commit fails
- Use INSERT ... ON CONFLICT DO NOTHING to handle gracefully
- Return existing record if duplicate detected

### Connection Pooling and Configuration

**SQLite Connection Settings:**
- `check_same_thread`: False (required for testing with multiple threads)
- `timeout`: 30 seconds (wait time for locks)
- Foreign keys: ON (enforce referential integrity if used later)

**Engine Configuration:**
- `pool_pre_ping`: True (verify connections before use)
- `echo`: False (set True for SQL debugging via LOG_LEVEL=DEBUG)
- `future`: True (use SQLAlchemy 2.0 API)

**Session Configuration:**
- `autocommit`: False (use explicit transactions)
- `autoflush`: True (flush changes before queries)
- `expire_on_commit`: False (keep objects accessible after commit)

### Error Handling Strategy

**Exception Hierarchy:**
```
PersistenceError (base)
├── DatabaseConnectionError (initialization/connection failures)
├── RecordNotFoundError (missing required records)
└── DataIntegrityError (constraint violations)
```

**Error Handling Principles:**
- Wrap SQLAlchemy exceptions in domain exceptions at repository boundary
- Log all database errors with context (SQL, parameters, traceback)
- Preserve original exception as cause (use `raise from`)
- Never expose SQL internals to callers
- Include actionable error messages (e.g., "Database file not writable at path X")

**Retry Strategy:**
- SQLite BUSY errors: Automatic retry with exponential backoff (built into driver)
- Connection failures: No automatic retry; let application decide
- Constraint violations: No retry; bubble up immediately

**Transaction Isolation:**
- SQLite default: DEFERRED (acquire lock on first write)
- Consider IMMEDIATE for write-heavy operations to prevent lock contention
- Not critical for v1.0 with single-threaded scheduler

## Testing Strategy

### Unit Tests

**Test File:** `tests/test_persistence.py`

**Test Categories:**

#### Database Initialization Tests
- Test successful initialization with valid database URL
- Test initialization creates parent directories if missing
- Test initialization with invalid URL raises DatabaseConnectionError
- Test schema migration is idempotent (can run multiple times)

#### Session Management Tests
- Test session context manager commits on success
- Test session context manager rolls back on exception
- Test session is closed after use
- Test multiple concurrent sessions don't interfere

#### JobRepository Tests
- Test get_by_key returns job when exists
- Test get_by_key returns None when not found
- Test get_by_source returns all jobs for source
- Test get_by_source returns empty list when none found
- Test upsert inserts new job
- Test upsert updates existing job (all fields)
- Test update_last_seen updates timestamp only
- Test bulk_upsert inserts multiple jobs efficiently
- Test get_stale_jobs returns jobs older than cutoff

#### SourceRepository Tests
- Test get_by_identifier returns source when exists
- Test get_all returns all sources
- Test upsert inserts new source
- Test upsert updates existing source
- Test update_success clears error state
- Test update_error preserves last_success_at

#### AlertRepository Tests
- Test has_been_sent returns False for new alert
- Test has_been_sent returns True for existing alert
- Test record_alert inserts new record
- Test record_alert is idempotent (handles duplicates gracefully)
- Test get_alerts_for_job returns all versions
- Test cleanup_old_alerts deletes old records

#### ORM Model Conversion Tests
- Test JobModel.to_domain() converts all fields correctly
- Test JobModel.from_domain() converts all fields correctly
- Test datetime conversion handles UTC correctly
- Test datetime conversion handles None values
- Test conversion for SourceStatusModel and AlertRecordModel

### Integration Tests

**Test File:** `tests/integration/test_persistence_integration.py`

**Test Scenarios:**

#### End-to-End Job Persistence
- Create job → upsert → retrieve → verify all fields match
- Upsert same job twice → verify single record exists
- Update job content_hash → upsert → verify updated
- Create multiple jobs → get_by_source → verify all returned

#### Change Detection Workflow
- Store job with content_hash_v1
- Check has_been_sent (job_key, content_hash_v1) → False
- Record alert (job_key, content_hash_v1)
- Check has_been_sent (job_key, content_hash_v1) → True
- Update job with content_hash_v2
- Check has_been_sent (job_key, content_hash_v2) → False

#### Source Health Tracking
- Initialize source via upsert
- Update with success → verify last_success_at set
- Update with error → verify error_message stored
- Update with success again → verify error cleared

#### Concurrent Access (if applicable)
- Multiple threads/processes reading and writing
- Verify no corruption or lost updates
- SQLite serializes writes, but test behavior

### Test Fixtures and Helpers

**Fixtures:**
- `test_db_engine`: Creates temporary SQLite database for testing
- `test_session`: Provides clean session for each test
- `sample_job`: Factory function to create test Job instances
- `sample_source_status`: Factory function for SourceStatus
- `sample_alert_record`: Factory function for AlertRecord

**Helper Functions:**
- `create_test_job(job_key=None, **overrides)`: Create Job with defaults
- `assert_job_equal(job1, job2, exclude=None)`: Compare jobs ignoring fields
- `clear_database(session)`: Delete all records from all tables

**Test Database Configuration:**
- Use in-memory SQLite (`:memory:`) for speed
- Alternative: temporary file for debugging (pytest tmp_path)
- Ensure each test starts with clean schema

### Test Coverage Goals

- Unit test coverage: 100% of persistence module
- Integration test coverage: All critical workflows
- Edge cases: Null values, empty strings, timezone edge cases
- Error paths: Connection failures, constraint violations, timeouts

## Public API

### Exports from app/persistence/__init__.py

```python
# Database initialization
from .database import init_database, get_session

# Repositories
from .repositories import JobRepository, SourceRepository, AlertRepository

# Exceptions
from .exceptions import (
    PersistenceError,
    DatabaseConnectionError,
    RecordNotFoundError,
    DataIntegrityError,
)

# Type hints (for external use)
from sqlalchemy.orm import Session
```

**Usage Example:**

```python
from app.persistence import init_database, get_session, JobRepository
from app.domain.models import Job

# Initialize (once at startup)
init_database("sqlite:///./data/job_scanner.db")

# Use repositories
with get_session() as session:
    job_repo = JobRepository(session)

    # Create/update job
    job = Job(...)
    persisted_job = job_repo.upsert(job)

    # Query job
    found_job = job_repo.get_by_key("abc123")
```

## Implementation Checklist

### Phase 1: Foundation (database.py, schema.py, exceptions.py)
- [ ] Define persistence exceptions in exceptions.py
- [ ] Implement database.py with engine creation and session management
- [ ] Define ORM models in schema.py (JobModel, SourceStatusModel, AlertRecordModel)
- [ ] Implement to_domain() and from_domain() conversion methods
- [ ] Implement schema migration function (create_all)
- [ ] Write unit tests for database initialization
- [ ] Write unit tests for session management
- [ ] Write unit tests for ORM model conversions

### Phase 2: Repositories (repositories.py)
- [ ] Implement JobRepository with all methods
- [ ] Implement SourceRepository with all methods
- [ ] Implement AlertRepository with all methods
- [ ] Write unit tests for JobRepository methods
- [ ] Write unit tests for SourceRepository methods
- [ ] Write unit tests for AlertRepository methods
- [ ] Verify error handling and exception wrapping

### Phase 3: Integration and Validation
- [ ] Create test fixtures and helpers
- [ ] Write integration tests for end-to-end workflows
- [ ] Write integration tests for change detection scenario
- [ ] Test with real SQLite file (not in-memory)
- [ ] Verify datetime handling and timezone correctness
- [ ] Test edge cases (null values, empty strings, long text)
- [ ] Run full test suite and achieve target coverage

### Phase 4: Documentation and Cleanup
- [ ] Update app/persistence/__init__.py with public exports
- [ ] Add docstrings to all public functions and classes
- [ ] Add type hints to all function signatures
- [ ] Create usage examples in docstrings
- [ ] Update main tech spec with persistence layer status
- [ ] Document any deviations or assumptions made during implementation

## Acceptance Criteria

### From PRD User Story 4: "Normalize, Detect Changes, and Persist"

✅ **Unique key = hash of source identifier + external job ID**
- Implemented via `compute_job_key(source_type, source_identifier, external_id)` in app/utils/hashing.py
- Used as primary key in jobs table
- Ensures jobs are unique across all sources

✅ **Distinguishes New vs Updated using ATS `updated_at` (when available)**
- Stored in jobs.updated_at column (nullable)
- Normalization layer sets from ATS data
- Persistence layer stores and retrieves for comparison

✅ **Persists `first_seen_at`, `last_seen_at`, key attributes, and alert state in SQLite**
- jobs table includes first_seen_at and last_seen_at
- alerts_sent table tracks alert state per job version
- All key attributes (title, description, location, url, etc.) persisted

✅ **Restarts do not re-alert already-alerted job versions**
- AlertRepository.has_been_sent() checks before sending
- Composite key (job_key, version_hash) ensures one alert per version
- Persisted state survives restarts

### Additional Acceptance Criteria

✅ **Database connection handling**
- init_database() creates engine and validates connection
- get_session() provides managed sessions with auto-commit/rollback
- Connection pooling configured appropriately for SQLite

✅ **Schema migration**
- create_schema() creates tables if not exist (idempotent)
- Includes all required indexes for performance
- Validates schema on initialization

✅ **Upsert and query helpers**
- JobRepository provides upsert, get, query methods
- SourceRepository tracks source health
- AlertRepository prevents duplicate alerts

✅ **Associated tests**
- Unit tests for all repository methods
- Integration tests for end-to-end workflows
- Edge case and error path coverage

## Dependencies and Prerequisites

### Required Before Implementation
- ✅ Domain models implemented (Job, AlertRecord, SourceStatus)
- ✅ Hashing utilities implemented (compute_job_key, compute_content_hash)
- ✅ Environment configuration with DATABASE_URL support
- ✅ SQLAlchemy added to project dependencies

### Blocked Until Complete
- ⏸ ATS Adapters (Step 5): Need JobRepository to persist fetched jobs
- ⏸ Matching Engine (Step 6): Need JobRepository to query jobs for matching
- ⏸ Notification Service (Step 7): Need AlertRepository to track sent alerts
- ⏸ Scheduler & Pipeline (Step 8): Need all repositories for orchestration

## Open Questions and Assumptions

### Assumptions Made

1. **SQLite Version**: Assume SQLite 3.24.0+ available for UPSERT support (ON CONFLICT clause)
   - Fallback: Use SELECT + INSERT/UPDATE pattern for older versions

2. **Single Process**: v1.0 runs single process, no need for advanced locking
   - Multi-process coordination deferred to future version

3. **Database Size**: Expect < 100K jobs in database; no partitioning needed
   - Indexes provide adequate performance for anticipated scale

4. **Datetime Format**: Store as ISO 8601 strings rather than SQLite REAL/INTEGER
   - Easier debugging and portability
   - Slight storage overhead acceptable for v1.0

5. **Foreign Keys**: Not enforced between jobs and alerts_sent
   - Simplifies testing and avoids cascade delete complexity
   - May add in future version

6. **Cleanup Strategy**: Manual cleanup of old jobs/alerts deferred to future
   - Repository methods provided but not invoked automatically
   - Operator can run cleanup scripts as needed

### Questions for Product Manager

1. **Data Retention**: How long should we keep jobs that are no longer posted?
   - Suggestion: 90 days for historical analysis
   - Impact: Need scheduled cleanup or archive strategy

2. **Alert History**: Should we keep all alert history or only recent?
   - Suggestion: Keep 1 year for debugging and metrics
   - Impact: Storage growth over time

3. **Database Backup**: Any requirements for backup/restore?
   - Suggestion: Document SQLite file backup process
   - Impact: Operational runbook needed

4. **Migration Path**: If schema changes in v2.0, what's the migration strategy?
   - Suggestion: Add Alembic in v2.0 for version-controlled migrations
   - Impact: No impact on v1.0, just document future path

## Performance Considerations

### Expected Performance (v1.0 Scale)

**Assumptions:**
- 50 sources, average 100 jobs per source = 5,000 jobs per scan
- Scan interval: 15 minutes = ~20,000 jobs per hour
- Database size: ~50K-100K jobs total (assuming 2-3 month retention)

**Benchmarks to Validate:**
- Bulk upsert 5,000 jobs: < 5 seconds
- Query all jobs for one source: < 100ms
- Check if alert sent (single job): < 10ms
- Full database scan: < 1 second

**Optimization Strategies:**
- Use bulk_upsert with SQLAlchemy bulk operations
- Minimize queries via efficient WHERE clauses
- Use indexes on commonly queried columns
- Consider PRAGMA optimization for SQLite (WAL mode, cache size)

### Future Scalability

**When to Consider Alternatives:**
- Database size > 1M jobs: Consider PostgreSQL or archival strategy
- Write throughput > 10K jobs/minute: Consider batching and connection pooling
- Multiple processes: Need distributed locking (Redis, PostgreSQL advisory locks)

**Migration Path:**
- SQLAlchemy abstraction allows database swap with minimal code changes
- Test suite validates behavior against different backends

## Security Considerations

### Database Security

1. **File Permissions**: SQLite file should be readable/writable by service only
   - Set file permissions to 600 (owner read/write only)
   - Verify in init_database() and warn if too permissive

2. **SQL Injection**: Use parameterized queries exclusively
   - SQLAlchemy ORM provides automatic parameterization
   - Never construct raw SQL with string concatenation

3. **Connection String**: Support DATABASE_URL from environment variable
   - Never log full connection strings (may contain credentials for future backends)
   - Redact sensitive parts in error messages

4. **Data Sanitization**: No sensitive data expected in job postings
   - Job descriptions may contain company-internal info; treat as confidential
   - No encryption at rest in v1.0 (SQLite limitation)

### Secrets Management

- DATABASE_URL may contain credentials in future (PostgreSQL)
- Current implementation (SQLite file path) has no credentials
- Document environment variable handling in security runbook

## Logging and Observability

### Log Events

**INFO Level:**
- Database initialized successfully with URL (redacted)
- Schema migration completed (list of tables created)
- Session opened/closed (in debug mode only)
- Repository operations (counts, high-level actions)

**WARNING Level:**
- Upsert conflict detected and resolved
- Query returned no results when expected (contextual)
- Connection pool exhausted (if using pooling)

**ERROR Level:**
- Database connection failed with reason
- SQL execution error with query and parameters (sanitized)
- Constraint violation with details
- Transaction rollback due to exception

**DEBUG Level:**
- SQL statements executed (via SQLAlchemy echo)
- Parameter values for queries
- ORM model conversion details

### Metrics (Future Enhancement)

Potential metrics to track:
- Database operation latency (percentiles)
- Query count per operation type
- Connection pool utilization
- Database file size over time
- Lock contention / busy errors

## Deployment Considerations

### Docker Integration

**Database File Location:**
- Default: `/data/job_scanner.db` inside container
- Mount external volume to `/data` for persistence
- Create directory with appropriate permissions in Dockerfile

**Dockerfile Changes:**
```dockerfile
# Create data directory for SQLite
RUN mkdir -p /data && chmod 700 /data

# Set environment variable default
ENV DATABASE_URL=sqlite:////data/job_scanner.db
```

**Volume Mount:**
```yaml
volumes:
  - ./data:/data
```

### Initialization on Startup

**Application Startup Sequence:**
1. Load environment configuration (including DATABASE_URL)
2. Initialize database connection
3. Run schema migration (create tables if needed)
4. Validate connection with test query
5. Initialize repositories (lazy instantiation)
6. Start scheduler

**Failure Handling:**
- If database initialization fails, exit with code 1 and clear error message
- Log full error details for debugging
- Provide actionable suggestions (e.g., "Ensure /data directory is writable")

### Health Checks

**Database Health Check:**
- Execute simple query (e.g., SELECT 1) to verify connection
- Check database file size and disk space
- Expose via health endpoint (future HTTP API)

**Startup Probe:**
- Verify database initialized successfully before marking ready
- Kubernetes/Docker health check can query this endpoint

## Related Documentation

- [Job Opportunity Scanner PRD](./job-opportunity-scanner-prd.md) - Product requirements
- [Job Opportunity Scanner Tech Spec](./job-opportunity-scanner-techspec.md) - Overall technical design
- [SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/) - ORM reference
- [SQLite Documentation](https://www.sqlite.org/docs.html) - Database reference

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-11-04 | System | Initial design specification |

---

**Next Steps:** Review this design with the team, clarify any open questions, and proceed with implementation following the checklist above.
