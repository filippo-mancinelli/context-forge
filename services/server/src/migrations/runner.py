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
