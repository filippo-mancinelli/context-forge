"""Repository come risorse del catalogo dell'organizzazione.

Un repository apparteneva a un progetto e aveva per chiave il nome: lo stesso
codice usato da due progetti era registrato e indicizzato due volte. Qui ogni
coppia URL-branch diventa un solo repository dell'organizzazione, chunk,
simboli, annotazioni e richieste di indicizzazione si legano al suo id e i
progetti lo selezionano. I repository elencati nella configurazione
dell'organizzazione vengono registrati nella tabella e tolti dalla
configurazione. Idempotente: sicura a ogni avvio.
"""
from __future__ import annotations

import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import asyncpg

from ..config import get_settings
from ..datasources.secrets import encrypt_secret
from .migration import _column_exists
from .names import normalize_repo_url, repo_name_for, unique_name

logger = logging.getLogger(__name__)

# Session advisory lock held for the whole conversion, clone moves included, so
# two instances booting together never run it concurrently ("CFRC").
REPO_CATALOG_LOCK_KEY = 0x43465243

EMPTY_REPORT: dict[str, Any] = {
    "imported": 0,
    "repos_before": 0,
    "repos_after": 0,
    "merged": [],
    "renamed": [],
    "chunks_deleted": 0,
    "symbols_deleted": 0,
    "annotations_merged": 0,
}

# (tabella, vincolo, definizione): aggiunti se mancano.
_REPO_CONSTRAINTS = (
    ("repos", "repos_pkey", "PRIMARY KEY (id)"),
    ("repo_chunks", "repo_chunks_repo_fkey", "FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE"),
    ("repo_chunks", "repo_chunks_repo_unique", "UNIQUE (repo_id, file_path, chunk_index)"),
    ("repo_symbols", "repo_symbols_repo_pkey", "PRIMARY KEY (repo_id, file_path, name, kind)"),
    ("repo_symbols", "repo_symbols_repo_fkey", "FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE"),
    ("chunk_annotations", "chunk_annotations_repo_fkey",
     "FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE"),
    ("index_requests", "index_requests_repo_fkey", "FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE"),
)

# Colonne che legavano i dati derivati al nome del repository e al progetto.
_LEGACY_COLUMNS = (
    ("repo_chunks", ("repo_name", "project_id")),
    ("repo_symbols", ("repo_name", "project_id")),
    ("chunk_annotations", ("repo_name", "project_id")),
    ("index_requests", ("repo_name",)),
    ("repos", ("project_id",)),
)


def _rowcount(status: Optional[str]) -> int:
    """Righe toccate secondo il tag di comando di asyncpg (``DELETE 12``, ``INSERT 0 1``)."""
    return int(status.split()[-1]) if status else 0


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


async def _constraint_exists(conn, table: str, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM pg_constraint WHERE conname = $1 AND conrelid = $2::regclass", name, table
        )
    )


async def _has_unowned_rows(conn) -> bool:
    """Legacy rows not attributed to an organization yet (install not set up)."""
    for table in ("repos", "repo_chunks", "index_requests"):
        if await _column_exists(conn, table, "org_id") and await conn.fetchval(
            f"SELECT 1 FROM {table} WHERE org_id IS NULL LIMIT 1"
        ):
            return True
    return False


async def _drop_name_primary_key(conn) -> None:
    """The baseline keys repos by name alone until an organization exists: drop that key."""
    columns = await conn.fetch(
        "SELECT a.attname FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
        "WHERE c.conname = 'repos_pkey' AND c.conrelid = 'repos'::regclass"
    )
    if columns and [r["attname"] for r in columns] != ["id"]:
        await conn.execute("ALTER TABLE repos DROP CONSTRAINT repos_pkey")


async def _ensure_selection_table(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_repos (
            project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            repo_id    BIGINT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            added_by   BIGINT REFERENCES admin_users(id) ON DELETE SET NULL,
            added_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (project_id, repo_id)
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS project_repos_repo_idx ON project_repos (repo_id)")


async def _ensure_repo_keys(conn) -> None:
    for table, name, definition in _REPO_CONSTRAINTS:
        if not await _constraint_exists(conn, table, name):
            await conn.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}")
    await _ensure_selection_table(conn)
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS repos_org_lower_name_idx ON repos (org_id, lower(name))"
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS repos_org_url_branch_idx "
        "ON repos (org_id, lower(url), branch) WHERE url IS NOT NULL"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS repo_symbols_repo_name_idx ON repo_symbols (repo_id, name, kind)"
    )


async def _default_projects(conn) -> dict[int, int]:
    """Progetto di default (il più vecchio) di ogni organizzazione."""
    rows = await conn.fetch(
        "SELECT DISTINCT ON (org_id) org_id, id FROM projects ORDER BY org_id, created_at, id"
    )
    return {r["org_id"]: r["id"] for r in rows}


async def _config_repos(conn) -> list[tuple[int, dict[str, Any]]]:
    """Repository elencati nelle configurazioni delle organizzazioni."""
    out: list[tuple[int, dict[str, Any]]] = []
    for row in await conn.fetch(
        "SELECT org_id, forge_config FROM org_runtime_config WHERE forge_config ? 'repos'"
    ):
        for repo in _decode(row["forge_config"]).get("repos") or []:
            if isinstance(repo, dict) and repo.get("name"):
                out.append((row["org_id"], repo))
    return out


async def _strip_config_repos(conn) -> int:
    stripped = _rowcount(
        await conn.execute(
            "UPDATE org_runtime_config SET forge_config = forge_config - 'repos' WHERE forge_config ? 'repos'"
        )
    )
    await conn.execute(
        "UPDATE app_runtime_config SET forge_config = forge_config - 'repos' WHERE forge_config ? 'repos'"
    )
    return stripped


async def _import_legacy(conn, config_repos, defaults: dict[int, int]) -> int:
    imported = 0
    for org_id, repo in config_repos:
        imported += _rowcount(
            await conn.execute(
                """
                INSERT INTO repos (org_id, project_id, name, type, url, path, branch, language, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')
                ON CONFLICT (org_id, name) DO NOTHING
                """,
                org_id, defaults.get(org_id), repo["name"], repo.get("type") or "local", repo.get("url"),
                repo.get("path"), repo.get("branch") or "main", repo.get("language") or "auto",
            )
        )
    return imported


async def _import_catalog(conn, config_repos, defaults: dict[int, int]) -> int:
    taken: dict[int, set[str]] = defaultdict(set)
    for r in await conn.fetch("SELECT org_id, lower(name) AS name FROM repos"):
        taken[r["org_id"]].add(r["name"])
    imported = 0
    for org_id, repo in config_repos:
        url = normalize_repo_url(repo.get("url"))
        branch = repo.get("branch") or "main"
        repo_id = await conn.fetchval(
            "SELECT id FROM repos WHERE org_id = $1 AND (lower(name) = lower($2) "
            "OR ($3::text IS NOT NULL AND lower(url) = lower($3) AND branch = $4))",
            org_id, repo["name"], url, branch,
        )
        if repo_id is None:
            name = unique_name(repo["name"], taken[org_id])
            repo_id = await conn.fetchval(
                "INSERT INTO repos (org_id, name, type, url, path, branch, language, token_enc, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending') RETURNING id",
                org_id, name, repo.get("type") or "local", url, repo.get("path"), branch,
                repo.get("language") or "auto", encrypt_secret(repo["token"]) if repo.get("token") else None,
            )
            taken[org_id].add(name.lower())
            imported += 1
        if defaults.get(org_id) is not None:
            await conn.execute(
                "INSERT INTO project_repos (project_id, repo_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                defaults[org_id], repo_id,
            )
    return imported


def _plan_groups(rows: list[dict[str, Any]]) -> list[tuple[list[dict[str, Any]], dict[str, Any], str]]:
    """(righe del gruppo, riga canonica, nome finale) per ogni repository risultante.

    I gruppi di una sola riga tengono il nome e lo prenotano per primi; un gruppo
    di più righe prende l'ultimo segmento dell'URL, con ``@branch`` se è già usato.
    """
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        url = row["normalized_url"]
        key = (row["org_id"], url.lower(), row["branch"] or "main") if url else (row["org_id"], "id", row["id"])
        groups[key].append(row)

    taken: dict[int, set[str]] = defaultdict(set)
    plan: list[tuple[list[dict[str, Any]], dict[str, Any], str]] = []
    for members in groups.values():
        if len(members) == 1:
            row = members[0]
            name = unique_name(row["name"], taken[row["org_id"]])
            taken[row["org_id"]].add(name.lower())
            plan.append((members, row, name))
    for members in sorted((g for g in groups.values() if len(g) > 1), key=lambda g: min(m["id"] for m in g)):
        first = members[0]
        name = repo_name_for(first["normalized_url"], first["branch"] or "main", taken[first["org_id"]])
        taken[first["org_id"]].add(name.lower())
        canonical = next((m for m in members if m["name"].lower() == name.lower()), None) or max(
            members, key=lambda m: (m["total_chunks"] or 0, -m["id"])
        )
        plan.append((members, canonical, name))
    return plan


async def _convert_legacy(
    conn, report: dict[str, Any], tokens: dict[tuple[int, str], str], defaults: dict[int, int]
) -> list[tuple[str, Path, Optional[Path]]]:
    """Converte i repository con chiave per nome.

    Ritorna le operazioni sulle copie locali da eseguire dopo il commit:
    ``("move", origine, destinazione)`` oppure ``("delete", percorso, None)``.
    """
    if not await _column_exists(conn, "repos", "id"):
        await conn.execute("ALTER TABLE repos ADD COLUMN id BIGSERIAL")
    project_column = "project_id" if await _column_exists(conn, "repos", "project_id") else "NULL::bigint AS project_id"
    rows = [
        dict(r)
        for r in await conn.fetch(
            f"SELECT id, org_id, name, type, url, path, branch, total_chunks, {project_column} "
            "FROM repos ORDER BY org_id, id"
        )
    ]
    for row in rows:
        row["normalized_url"] = normalize_repo_url(row["url"]) if row["type"] != "local" else None
    report["repos_before"] = len(rows)
    plan = _plan_groups(rows)

    # La chiave per nome lascia il posto all'id prima di rinominare.
    await conn.execute("ALTER TABLE repos DROP CONSTRAINT IF EXISTS repos_org_name_pkey")
    await _drop_name_primary_key(conn)
    if not await _constraint_exists(conn, "repos", "repos_pkey"):
        await conn.execute("ALTER TABLE repos ADD CONSTRAINT repos_pkey PRIMARY KEY (id)")
    await _ensure_selection_table(conn)

    cache = Path(get_settings().repos_cache_dir)
    clone_ops: list[tuple[str, Path, Optional[Path]]] = []
    for members, canonical, name in plan:
        org_id = canonical["org_id"]
        duplicates = [m for m in members if m["id"] != canonical["id"]]
        names = [m["name"] for m in members]
        if name != canonical["name"]:
            report["renamed"].append(f"repos#{canonical['id']}: {canonical['name']} -> {name}")
        # Una riga locale non ha URL: lasciarle quello grezzo farebbe collidere
        # repos_org_url_branch_idx con un remoto che ha per caso lo stesso URL e
        # branch. Un remoto porta sempre un branch esplicito: il raggruppamento
        # tratta già NULL come "main", l'indice unico e find_repo confrontano il
        # valore grezzo.
        is_local = canonical["type"] == "local"
        await conn.execute(
            "UPDATE repos SET name = $1, url = $2, branch = $3 WHERE id = $4",
            name,
            None if is_local else (canonical["normalized_url"] or canonical["url"]),
            canonical["branch"] if is_local else (canonical["branch"] or "main"),
            canonical["id"],
        )
        for member in members:
            project_id = member["project_id"] or defaults.get(org_id)
            if project_id is not None:
                await conn.execute(
                    "INSERT INTO project_repos (project_id, repo_id) "
                    "SELECT $1, $2 WHERE EXISTS (SELECT 1 FROM projects WHERE id = $1) ON CONFLICT DO NOTHING",
                    project_id, canonical["id"],
                )
        await conn.execute(
            "UPDATE chunk_annotations SET repo_id = $1 WHERE org_id = $2 AND repo_name = ANY($3::text[])",
            canonical["id"], org_id, names,
        )
        await conn.execute(
            "UPDATE index_requests SET repo_id = $1 WHERE org_id = $2 AND repo_name = ANY($3::text[])",
            canonical["id"], org_id, names,
        )
        for dup in duplicates:
            report["chunks_deleted"] += _rowcount(
                await conn.execute("DELETE FROM repo_chunks WHERE org_id = $1 AND repo_name = $2", org_id, dup["name"])
            )
            if dup["project_id"] is not None:
                report["symbols_deleted"] += _rowcount(
                    await conn.execute(
                        "DELETE FROM repo_symbols WHERE project_id = $1 AND repo_name = $2",
                        dup["project_id"], dup["name"],
                    )
                )
        await conn.execute(
            "UPDATE repo_chunks SET repo_id = $1 WHERE org_id = $2 AND repo_name = $3",
            canonical["id"], org_id, canonical["name"],
        )
        if canonical["project_id"] is not None:
            await conn.execute(
                "UPDATE repo_symbols SET repo_id = $1 WHERE project_id = $2 AND repo_name = $3",
                canonical["id"], canonical["project_id"], canonical["name"],
            )
        if duplicates:
            await conn.execute("DELETE FROM repos WHERE id = ANY($1::bigint[])", [d["id"] for d in duplicates])
            report["merged"].append({"name": name, "rows": len(members), "repo_id": canonical["id"]})
        if canonical["type"] != "local":
            org_dir = cache / f"org_{org_id}"
            clone_ops.append(("move", org_dir / canonical["name"], org_dir / f"repo_{canonical['id']}"))
            clone_ops.extend(("delete", org_dir / d["name"], None) for d in duplicates)

    by_original = {(m["org_id"], m["name"]): canonical["id"] for members, canonical, _ in plan for m in members}
    for (org_id, name), token in tokens.items():
        repo_id = by_original.get((org_id, name))
        if repo_id is not None:
            await conn.execute(
                "UPDATE repos SET token_enc = $1 WHERE id = $2 AND coalesce(token_enc, '') = ''",
                encrypt_secret(token), repo_id,
            )

    report["annotations_merged"] = _rowcount(
        await conn.execute(
            """
            DELETE FROM chunk_annotations a USING chunk_annotations b
             WHERE a.repo_id = b.repo_id AND a.file_path = b.file_path
               AND a.start_line IS NOT DISTINCT FROM b.start_line
               AND a.end_line IS NOT DISTINCT FROM b.end_line
               AND a.note = b.note AND a.id > b.id
            """
        )
    )
    # Dati derivati che non corrispondono più a nessun repository.
    report["chunks_deleted"] += _rowcount(await conn.execute("DELETE FROM repo_chunks WHERE repo_id IS NULL"))
    report["symbols_deleted"] += _rowcount(await conn.execute("DELETE FROM repo_symbols WHERE repo_id IS NULL"))
    await conn.execute("DELETE FROM chunk_annotations WHERE repo_id IS NULL")
    await conn.execute("DELETE FROM index_requests WHERE repo_id IS NULL AND repo_name IS NOT NULL")
    # I simboli di un repository restato "indexed" erano registrati sotto un
    # progetto diverso da quello del repository e sono andati persi come
    # orfani: senza indexed_commit la prossima indicizzazione riparte intera e
    # ricostruisce il grafo (i chunk restano, gli embedding si riusano per hash
    # del contenuto).
    await conn.execute(
        """
        UPDATE repos SET indexed_commit = NULL
         WHERE status = 'indexed' AND total_chunks > 0
           AND NOT EXISTS (SELECT 1 FROM repo_symbols WHERE repo_symbols.repo_id = repos.id)
        """
    )

    await conn.execute("ALTER TABLE repo_chunks DROP CONSTRAINT IF EXISTS repo_chunks_org_unique")
    for table, columns in _LEGACY_COLUMNS:
        for column in columns:
            await conn.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
    for table in ("repo_chunks", "repo_symbols", "chunk_annotations"):
        await conn.execute(f"ALTER TABLE {table} ALTER COLUMN repo_id SET NOT NULL")
    # Before setup the baseline leaves org_id nullable; _has_unowned_rows ruled out NULLs.
    for table in ("repos", "repo_chunks"):
        await conn.execute(f"ALTER TABLE {table} ALTER COLUMN org_id SET NOT NULL")
    # Una richiesta dal webhook o dal catalogo non ha un progetto richiedente.
    await conn.execute("ALTER TABLE index_requests ALTER COLUMN project_id DROP NOT NULL")
    report["repos_after"] = await conn.fetchval("SELECT count(*) FROM repos")
    return clone_ops


def _apply_clone_ops(ops: list[tuple[str, Path, Optional[Path]]]) -> None:
    """Sposta o cancella le copie locali dopo il commit: un errore qui costa solo un nuovo clone."""
    for op, source, target in ops:
        try:
            if op == "move" and target is not None and source.is_dir() and not target.exists():
                source.rename(target)
            elif op == "delete" and source.is_dir():
                shutil.rmtree(source)
        except OSError as e:
            logger.warning("Repository copy %s %s failed: %s", op, source, e)


async def apply_repo_catalog_migration() -> dict[str, Any]:
    """Converte i repository con chiave per nome, importa quelli della configurazione
    e garantisce chiavi e selezioni. Ritorna il report della conversione.

    Usa una connessione dedicata invece di una del pool condiviso (db.get_pool,
    command_timeout=60): su un catalogo con molti repository le istruzioni che
    aggiungono chiave primaria e foreign key sulle tabelle derivate (repo_chunks,
    repo_symbols) possono superare quel timeout lato client e far fallire l'intera
    transazione di conversione.
    """
    report: dict[str, Any] = {**EMPTY_REPORT, "merged": [], "renamed": []}
    clone_ops: list[tuple[str, Path, Optional[Path]]] = []
    conn = await asyncpg.connect(get_settings().database_url, command_timeout=None)
    try:
        await conn.execute("SELECT pg_advisory_lock($1::bigint)", REPO_CATALOG_LOCK_KEY)
        try:
            async with conn.transaction():
                legacy = await _column_exists(conn, "repo_chunks", "repo_name")
                if legacy and await _has_unowned_rows(conn):
                    # Converted once the default organization owns these rows.
                    logger.info("Repository catalog migration deferred: rows without an organization")
                    return report
                config_repos = await _config_repos(conn)
                defaults = await _default_projects(conn)
                if legacy:
                    report["imported"] = await _import_legacy(conn, config_repos, defaults)
                    tokens = {(org_id, r["name"]): r["token"] for org_id, r in config_repos if r.get("token")}
                    clone_ops = await _convert_legacy(conn, report, tokens, defaults)
                await _ensure_repo_keys(conn)
                if not legacy and config_repos:
                    report["imported"] = await _import_catalog(conn, config_repos, defaults)
                stripped = await _strip_config_repos(conn)
            if stripped:
                from .. import org_config

                org_config.invalidate()
            _apply_clone_ops(clone_ops)
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1::bigint)", REPO_CATALOG_LOCK_KEY)
            except Exception:  # closing the session releases it anyway
                logger.warning("Could not release the repository catalog lock", exc_info=True)
    finally:
        await conn.close()
    if report != EMPTY_REPORT:
        logger.info("Repository catalog migration: %s", report)
    return report
