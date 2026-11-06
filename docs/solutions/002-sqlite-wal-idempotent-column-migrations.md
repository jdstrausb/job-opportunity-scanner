---
id: SOL-002
title: Safe Idempotent Column Additions with SQLite WAL
date: 2026-04-06
tags: [persistence, sqlite, migrations, sqlalchemy]
---

## Context

When evolving an existing SQLite schema in a self-hosted single-file architecture (such as `data/job_scanner.db`), deployments run `create_schema(engine)` during service bootstrapping. Under SQLAlchemy, `Base.metadata.create_all(engine, checkfirst=True)` automatically creates newly registered tables (such as `dismissed_items`).

However, `create_all()` silently ignores missing columns on pre-existing tables. When newly modeled nullable columns (such as `secondary_locations`, `is_remote`, or `location_country` on `jobs`) were introduced, existing databases lacked those columns. Subsequent queries or updates threw runtime errors (`sqlite3.OperationalError: no such column: jobs.secondary_locations`). Introducing heavy migration frameworks like Alembic for lightweight SQLite microservices added unnecessary dependency complexity, but relying solely on `create_all()` caused breaking schema drift across restarts.

## Solution

A lightweight, idempotent column migration mechanism was introduced in `app/persistence/schema.py` to run alongside `Base.metadata.create_all()`:

1. **Declared Migration Map**: Define column specifications for columns added to pre-existing tables after their initial release:
   ```python
   _JOBS_MIGRATION_COLUMNS = {
       "secondary_locations": "JSON",
       "is_remote": "BOOLEAN",
       "location_country": "VARCHAR(255)",
   }
   ```
2. **Schema Inspection and Selective `ALTER TABLE`**: Inspect the current table columns via `sqlalchemy.inspect(engine).get_columns("jobs")` and generate `ALTER TABLE ... ADD COLUMN` statements inside an explicit transaction:
   ```python
   def _ensure_table_columns(engine: Engine) -> None:
       from sqlalchemy import inspect, text

       existing = {col["name"] for col in inspect(engine).get_columns("jobs")}
       missing = {n: t for n, t in _JOBS_MIGRATION_COLUMNS.items() if n not in existing}
       if not missing:
           return

       with engine.begin() as conn:
           for name, sql_type in missing.items():
               logger.info(f"Adding missing column jobs.{name}")
               conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}"))
   ```
3. **Bootstrapping Integration**: Invoke `_ensure_table_columns(engine)` immediately after `Base.metadata.create_all(engine, checkfirst=True)` in `create_schema(engine)`.

Because SQLite in WAL mode permits concurrent readers but enforces single-writer serialization, using `with engine.begin() as conn:` guarantees atomic column additions without table locks interfering with subsequent reader initialization.

## Friction Points & Gotchas

- **SQLite `ALTER TABLE` Constraints**: SQLite supports adding nullable columns or columns with constant default values via `ALTER TABLE ADD COLUMN`. It does not support adding columns with complex expressions or `NOT NULL` without a default value. All migrated columns must be declared nullable or provide appropriate constant defaults.
- **Connection Locking under WAL**: Executing schema modifications across loose connections outside of `engine.begin()` can cause `sqlite3.OperationalError: database is locked` if active read transactions are holding database locks. Wrapping inspection and DDL executions cleanly ensures locks release immediately.
- **Table Absence on Fresh Databases**: Running inspection on a table before `Base.metadata.create_all()` causes inspection errors if the table does not exist. Calling `create_all()` first guarantees the base table exists before inspecting for missing columns.

## Extension: Primary Key Changes (2026-11-06)

`ALTER TABLE` cannot change a primary key in SQLite, so a column map is not enough when a key changes. The `sources` table moved from `PRIMARY KEY (source_identifier)` to `PRIMARY KEY (source_identifier, source_type)` so that one slug can exist on several ATS types. `_ensure_sources_primary_key(engine)` runs after `_ensure_table_columns()` and:

1. Compares `inspect(engine).get_pk_constraint("sources")` with the model's primary key. If they match, it returns (idempotent).
2. Inside one `engine.begin()` transaction: drops any leftover `sources_rebuild` table from an interrupted attempt, creates `sources_rebuild` from `Table.to_metadata(MetaData(), name=...)` so the DDL matches the model, copies every row with `INSERT ... SELECT`, drops `sources`, and renames `sources_rebuild` to `sources`.

The copy cannot conflict, because rows that are unique on the old key are also unique on any wider key that contains it. Narrowing a key is different: it needs an explicit de-duplication rule first. Get the table from `Base.metadata.tables[...]` and not from `Model.__table__`, which mypy types as `FromClause`. Tests: `test_create_schema_rebuilds_legacy_sources_primary_key`.

## Reusable Insight

Never rely on ORM `create_all()` helpers to perform schema migrations on pre-existing tables. For lightweight SQLite-backed services without heavyweight migration tools, pair `create_all()` with an idempotent schema inspection and targeted `ALTER TABLE ADD COLUMN` helper executed within an explicit transaction.
