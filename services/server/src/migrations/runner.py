"""Discovers, orders and applies the numbered migration modules."""
from __future__ import annotations

import importlib.util
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_LOCK_KEY = 0x43464D47

VERSIONS_DIR = Path(__file__).parent / "versions"

_FILENAME_RE = re.compile(r"^(\d{4})_([A-Za-z0-9_]+)\.py$")

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INT PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def discover_versions(versions_dir=None) -> list:
    """Load every versions/NNNN_<name>.py module, sorted by VERSION."""
    directory = Path(versions_dir) if versions_dir is not None else VERSIONS_DIR
    modules = []
    names: dict[int, str] = {}
    for path in sorted(directory.glob("*.py")):
        if not _FILENAME_RE.match(path.name):
            continue
        spec = importlib.util.spec_from_file_location(
            f"src.migrations.versions.{path.stem}", path
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for attr in ("VERSION", "NAME", "upgrade"):
            if not hasattr(module, attr):
                raise ValueError(f"{path.name}: migration module lacks {attr}")
        if module.VERSION in names:
            raise ValueError(
                f"{path.name}: duplicate migration version {module.VERSION} (also {names[module.VERSION]})"
            )
        names[module.VERSION] = path.name
        modules.append(module)
    modules.sort(key=lambda module: module.VERSION)
    return modules


async def current_version(pool) -> int:
    """Highest recorded version, 0 when schema_migrations does not exist."""
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
        )
        if not exists:
            return 0
        return await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ) or 0


async def _record(conn, module) -> None:
    await conn.execute(
        "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)"
        " ON CONFLICT (version) DO NOTHING",
        module.VERSION,
        module.NAME,
    )


async def run_migrations(pool) -> list[int]:
    """Apply every pending version module under the advisory lock."""
    applied: list[int] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1::bigint)", MIGRATIONS_LOCK_KEY)
        try:
            await conn.execute(SCHEMA_MIGRATIONS_DDL)
            version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ) or 0
            for module in discover_versions():
                if module.VERSION <= version:
                    continue
                if getattr(module, "TRANSACTIONAL", True):
                    async with conn.transaction():
                        await module.upgrade(conn)
                        await _record(conn, module)
                else:
                    await module.upgrade(conn)
                    await _record(conn, module)
                applied.append(module.VERSION)
                logger.info("Applied migration %04d_%s", module.VERSION, module.NAME)
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock($1::bigint)", MIGRATIONS_LOCK_KEY
            )
    return applied
