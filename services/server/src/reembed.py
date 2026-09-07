"""Ricalcolo degli embedding di un'organizzazione dal contenuto memorizzato.

Serve dopo un cambio di configurazione embeddings dell'org: i chunk di repo,
knowledge base e web vengono ri-embeddati a lotti senza ri-acquisire i
contenuti. Lo stato è tracciato su una riga della tabella jobs.
"""
from __future__ import annotations

import json
import logging

from .db import get_pool
from .indexer.embedder import embed_batch

logger = logging.getLogger(__name__)

_TABLES = ("repo_chunks", "kb_chunks", "web_chunks")
_BATCH = 50


async def _iter_chunks(table: str, org_id: int, batch_size: int):
    pool = await get_pool()
    last_id = 0
    while True:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, content FROM {table} "
                "WHERE org_id = $1 AND id > $2 ORDER BY id LIMIT $3",
                org_id, last_id, batch_size,
            )
        if not rows:
            return
        last_id = rows[-1]["id"]
        yield [(r["id"], r["content"]) for r in rows]


async def _update_embeddings(table: str, pairs: list[tuple[int, list[float]]], org_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            f"UPDATE {table} SET embedding = $2::vector WHERE id = $1 AND org_id = $3",
            [
                (chunk_id, "[" + ",".join(str(x) for x in vec) + "]", org_id)
                for chunk_id, vec in pairs
            ],
        )


async def _set_job_status(job_id: str, status: str, result=None, error=None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET status = $2, result = $3::jsonb, error_message = $4, "
            "updated_at = NOW() WHERE id = $1::uuid",
            job_id, status, json.dumps(result) if result is not None else None, error,
        )


async def reembed_org(org_id: int, job_id: str) -> dict:
    """Ri-embedda tutti i chunk dell'org; ritorna i conteggi per tabella."""
    counts: dict[str, int] = {}
    await _set_job_status(job_id, "running")
    try:
        for table in _TABLES:
            done = 0
            async for batch in _iter_chunks(table, org_id, _BATCH):
                vectors = await embed_batch([text for _, text in batch], org_id)
                await _update_embeddings(
                    table, [(cid, vec) for (cid, _), vec in zip(batch, vectors)], org_id
                )
                done += len(batch)
            counts[table] = done
        await _set_job_status(job_id, "completed", result=counts)
    except Exception as exc:
        logger.exception("org_reembed failed (org=%s)", org_id)
        await _set_job_status(job_id, "failed", error=str(exc))
    return counts
