# Versioned schema migrations — design

## Problem

`src/db.py` runs one idempotent DDL string (`SCHEMA_SQL`, ~800 lines) at every
boot, plus Python-side migrations (`apply_tenant_repo_migration`,
`apply_project_migration` in `src/tenancy.py`). It now performs backfills,
constraint swaps and `DROP TABLE`s. Every new feature edits the same string,
nothing records what already ran, and there is no way to see a database's
schema version.

## Design

### Runner

New package `src/migrations/`:

- `runner.py`
  - `MIGRATIONS_LOCK_KEY = 0x43464D47` (advisory lock key, constant).
  - `async def run_migrations(pool) -> list[int]`: takes
    `pg_advisory_lock(MIGRATIONS_LOCK_KEY)` on a dedicated connection, creates
    `schema_migrations` if missing, reads applied versions, discovers modules in
    `versions/` (files named `NNNN_<name>.py`, sorted by number), applies every
    module whose version is not yet recorded, in version order, records a row
    per module, releases the lock. Returns the versions applied in this run.
  - Each version module exposes `VERSION: int`, `NAME: str`,
    `TRANSACTIONAL: bool = True`, `async def upgrade(conn) -> None`.
    Transactional modules run inside `async with conn.transaction()`; a
    non-transactional module (needed for `CREATE INDEX CONCURRENTLY`) runs in
    autocommit and must be idempotent.
  - A failing module raises; nothing after it runs; the boot fails loudly.
  - `async def current_version(pool) -> int` (0 when the table is absent).
- `versions/0001_baseline.py`: `VERSION = 1`, `NAME = "baseline"`,
  `upgrade` executes `src.db.SCHEMA_SQL` verbatim (the string stays in `db.py`
  and is frozen: the module docstring says new changes go in new versions).
- Table: `schema_migrations(version INT PRIMARY KEY, name TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`.

### Boot integration

`src/db.py:init_db()` stops executing `SCHEMA_SQL` directly and calls
`run_migrations(pool)`. The Python-side tenant/project migrations keep running
after it, exactly where they run today (`ensure_tenant_storage`): they depend
on runtime state (default org) and stay idempotent.

Existing installs: `schema_migrations` is absent, so `0001_baseline` runs once
against an already-migrated database. `SCHEMA_SQL` is idempotent, so this is a
no-op apart from recording version 1.

### CLI

`src/cli.py` gains `migrate` (apply pending and print applied versions) and
`migrate --status` (print current version and pending modules). Both use
`DATABASE_URL` like the rest of the CLI.

### Developer guidance

README "Development" subsection (new, short): schema changes are modules in
`src/migrations/versions/NNNN_<name>.py`; never edit `SCHEMA_SQL`; module
template shown.

## Tests (no live DB)

- Runner discovers and orders modules from a temp directory (monkeypatch the
  versions path), applies only those above the recorded version, records rows,
  and stops at the first failure.
- Transactional vs non-transactional dispatch (fake connection records whether
  `transaction()` was entered).
- Advisory lock acquired and released, also on failure.
- `0001_baseline.upgrade` executes `SCHEMA_SQL` (fake conn captures the SQL).
- `init_db` calls the runner (monkeypatch).
