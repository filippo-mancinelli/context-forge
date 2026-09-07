"""Baseline schema, frozen: replays src.db.DDL as it stood at version 1.

Never edit this module or src.db.DDL. Every schema change from here on is a
new version module, src/migrations/versions/NNNN_<name>.py.
"""
from __future__ import annotations

from src.config import get_settings
from src.db import DDL

VERSION = 1
NAME = "baseline"
TRANSACTIONAL = True


async def upgrade(conn) -> None:
    await conn.execute(DDL.format(dims=get_settings().embeddings_dims))
