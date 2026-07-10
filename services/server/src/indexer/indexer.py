"""Repository indexer — tree-sitter parsing + pgvector storage."""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Iterator

from ..config import IndexingConfig, RepoConfig
from ..db import get_pool
from .embedder import embed_batch
from .git_manager import (
    commit_exists,
    ensure_repo_cloned,
    get_changed_files,
    get_head_commit,
    get_repo_local_path,
)

logger = logging.getLogger(__name__)

# File extensions supported by tree-sitter parsers
PARSEABLE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java"}
TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".yaml", ".yml", ".toml", ".json", ".env",
    ".sh", ".bash", ".sql", ".css", ".html", ".xml", ".ini", ".cfg",
    ".dockerfile", ".gitignore", ".proto",
}
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".pdf", ".zip", ".tar", ".gz", ".mp4", ".mp3",
    ".exe", ".dll", ".so", ".dylib", ".class", ".jar",
}


def _vector_to_pg(embedding: list[float]) -> str:
    """Convert embedding values to pgvector literal string."""
    return "[" + ",".join(f"{float(v):.10f}" for v in embedding) + "]"


def _get_parser(language: str):
    """Get a tree-sitter parser for the given language. Returns None if unsupported."""
    try:
        import tree_sitter_python as tspython
        import tree_sitter_javascript as tsjavascript
        import tree_sitter_typescript as tstypescript
        import tree_sitter_go as tsgo
        import tree_sitter_java as tsjava
        from tree_sitter import Language, Parser

        lang_map = {
            "python": tspython.language(),
            "javascript": tsjavascript.language(),
            "typescript": tstypescript.language_typescript(),
            "tsx": tstypescript.language_tsx(),
            "go": tsgo.language(),
            "java": tsjava.language(),
        }
        if language not in lang_map:
            return None
        parser = Parser(Language(lang_map[language]))
        return parser
    except Exception as e:
        logger.debug("tree-sitter parser unavailable for %s: %s", language, e)
        return None


def _detect_language(path: Path) -> str:
    """Detect programming language from file extension."""
    ext = path.suffix.lower()
    ext_map = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "tsx", ".go": "go", ".java": "java",
    }
    return ext_map.get(ext, "text")


def _extract_chunks_treesitter(content: str, language: str, config: IndexingConfig) -> list[dict]:
    """Extract semantic chunks (functions, classes) using tree-sitter."""
    parser = _get_parser(language)
    if not parser:
        return []

    try:
        tree = parser.parse(bytes(content, "utf-8"))
        chunks = []
        content_lines = content.splitlines()

        # Query for top-level declarations
        node_types = {
            "python": ["function_definition", "class_definition", "decorated_definition"],
            "javascript": ["function_declaration", "class_declaration", "arrow_function", "method_definition"],
            "typescript": ["function_declaration", "class_declaration", "interface_declaration", "type_alias_declaration"],
            "tsx": ["function_declaration", "class_declaration", "jsx_element"],
            "go": ["function_declaration", "method_declaration", "type_declaration"],
            "java": ["class_declaration", "method_declaration", "interface_declaration"],
        }
        target_types = set(node_types.get(language, []))

        def walk(node, depth=0):
            if node.type in target_types and depth <= 2:
                start_line = node.start_point[0]
                end_line = node.end_point[0]
                chunk_content = "\n".join(content_lines[start_line:end_line + 1])
                # Skip tiny chunks
                if len(chunk_content.strip()) < 30:
                    return
                # Extract name from first child
                name = None
                for child in node.children:
                    if child.type in ("identifier", "name"):
                        name = content[child.start_byte:child.end_byte]
                        break
                chunks.append({
                    "type": node.type,
                    "name": name,
                    "start_line": start_line,
                    "end_line": end_line,
                    "content": chunk_content,
                })
                return  # Don't recurse into parsed chunks
            for child in node.children:
                walk(child, depth + 1)

        walk(tree.root_node)
        return chunks
    except Exception as e:
        logger.debug("tree-sitter parse error: %s", e)
        return []


def _sliding_window_chunks(content: str, chunk_size: int, overlap: int) -> list[dict]:
    """Split text into overlapping chunks by approximate token count (chars/4)."""
    char_size = chunk_size * 4
    char_overlap = overlap * 4
    chunks = []
    start = 0
    while start < len(content):
        end = min(start + char_size, len(content))
        # Try to break at newline
        if end < len(content):
            nl = content.rfind("\n", start, end)
            if nl > start:
                end = nl
        chunks.append({"type": "text", "content": content[start:end], "start_line": 0, "end_line": 0})
        start += char_size - char_overlap
    return chunks


def _chunk_file(
    repo_name: str,
    file_path: Path,
    rel_path: str,
    indexing_cfg: "IndexingConfig",
    language: str | None,
) -> list[dict]:
    """Read and chunk a single file. Returns chunk dicts ready for insertion."""
    try:
        raw = file_path.read_bytes()
    except Exception:
        return []

    # NUL bytes mean binary content regardless of extension (same heuristic as
    # git); Postgres TEXT also rejects NUL outright, so strip any stragglers.
    if b"\x00" in raw[:8192]:
        return []
    content = raw.decode("utf-8", errors="replace").replace("\x00", "")

    detected_lang = language or _detect_language(file_path)
    suffix = file_path.suffix.lower()
    chunks: list[dict] = []

    if suffix in PARSEABLE_EXTENSIONS:
        ts_chunks = _extract_chunks_treesitter(content, detected_lang, indexing_cfg)
        if ts_chunks:
            for i, c in enumerate(ts_chunks):
                chunks.append({
                    "repo_name": repo_name,
                    "file_path": rel_path,
                    "chunk_index": i,
                    "chunk_type": c["type"],
                    "content": c["content"],
                    "metadata": json.dumps({"name": c.get("name"), "start_line": c.get("start_line")}),
                })
            return chunks

    sw_chunks = _sliding_window_chunks(content, indexing_cfg.chunk_size, indexing_cfg.chunk_overlap)
    for i, c in enumerate(sw_chunks):
        chunks.append({
            "repo_name": repo_name,
            "file_path": rel_path,
            "chunk_index": i,
            "chunk_type": c["type"],
            "content": c["content"],
            "metadata": json.dumps({"start_line": c.get("start_line", 0)}),
        })
    return chunks


def _collect_chunks_sync(
    repo_name: str,
    local_path: str,
    indexing_cfg: "IndexingConfig",
    language: str | None,
) -> list[dict]:
    """Collect and parse all chunks from a repo synchronously (run in thread pool)."""
    all_chunks: list[dict] = []
    files_processed = 0
    started_at = time.monotonic()

    for file_path, rel_path in _iter_repo_files(local_path, indexing_cfg):
        files_processed += 1
        all_chunks.extend(_chunk_file(repo_name, file_path, rel_path, indexing_cfg, language))

        if files_processed % 200 == 0:
            logger.info(
                "Chunking progress for %s: files=%d chunks=%d elapsed=%.1fs",
                repo_name,
                files_processed,
                len(all_chunks),
                time.monotonic() - started_at,
            )

    logger.info(
        "Chunking complete for %s: files=%d chunks=%d elapsed=%.1fs",
        repo_name,
        files_processed,
        len(all_chunks),
        time.monotonic() - started_at,
    )

    return all_chunks


def _collect_changed_chunks_sync(
    repo_name: str,
    local_path: str,
    indexing_cfg: "IndexingConfig",
    language: str | None,
    changed_paths: list[str],
) -> tuple[list[dict], list[str]]:
    """Chunk only the given changed paths (run in thread pool).

    Returns ``(chunks, indexed_paths)`` where ``indexed_paths`` are the paths that
    still exist and are indexable — i.e. produced at least one chunk. A changed
    path that was deleted, is no longer indexable, or yields no chunks is simply
    omitted (its stale chunks are removed separately by the caller).
    """
    root = Path(local_path)
    chunks: list[dict] = []
    indexed_paths: list[str] = []

    for rel in changed_paths:
        file_path = root / rel
        if not file_path.is_file() or not _is_indexable(rel, file_path, indexing_cfg):
            continue
        file_chunks = _chunk_file(repo_name, file_path, rel, indexing_cfg, language)
        if file_chunks:
            chunks.extend(file_chunks)
            indexed_paths.append(rel)

    return chunks, indexed_paths


def _should_exclude(rel_path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(f"/{rel_path}", pattern):
            return True
        # Check each path component
        parts = Path(rel_path).parts
        for part in parts:
            if fnmatch.fnmatch(part, pattern.strip("**/").strip("/*")):
                return True
    return False


def _is_indexable(rel_path: str, file_path: Path, config: IndexingConfig) -> bool:
    """Return True if a single file should be indexed under the given config.

    Shared by the full-tree walk and the incremental (changed-files) path so both
    apply identical exclude/size/extension rules.
    """
    if _should_exclude(rel_path, config.exclude):
        return False
    suffix = file_path.suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return False
    try:
        if file_path.stat().st_size > config.max_file_size_kb * 1024:
            return False
    except OSError:
        return False
    return suffix in PARSEABLE_EXTENSIONS or suffix in TEXT_EXTENSIONS


def _iter_repo_files(repo_path: str, config: IndexingConfig) -> Iterator[tuple[Path, str]]:
    """Yield (absolute_path, relative_path) for all indexable files in a repo."""
    root = Path(repo_path)
    scanned = 0
    yielded = 0

    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        rel_dir = current_path.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir

        # Prune excluded directories early to avoid traversing huge trees.
        kept_dirs: list[str] = []
        for d in dirnames:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if _should_exclude(rel, config.exclude):
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for filename in filenames:
            scanned += 1
            file_path = current_path / filename
            rel = file_path.relative_to(root).as_posix()

            if not _is_indexable(rel, file_path, config):
                continue

            yielded += 1
            if scanned % 5000 == 0:
                logger.info(
                    "File scan progress for %s: scanned=%d indexable=%d",
                    root.name,
                    scanned,
                    yielded,
                )
            yield file_path, rel

    logger.info("File scan complete for %s: scanned=%d indexable=%d", root.name, scanned, yielded)


async def _embed_chunks(chunks: list[dict], repo_name: str) -> list[list[float]]:
    """Embed chunk contents in batches, logging progress. Returns one vector per chunk."""
    texts = [c["content"] for c in chunks]
    batch_size = 20
    embeddings: list[list[float]] = []
    started_at = time.monotonic()
    total_batches = (len(texts) + batch_size - 1) // batch_size
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        logger.info(
            "Embedding progress for %s: batch=%d/%d size=%d",
            repo_name, (i // batch_size) + 1, total_batches, len(batch),
        )
        embeddings.extend(await embed_batch(batch))
    logger.info(
        "Embedding complete for %s: vectors=%d elapsed=%.1fs",
        repo_name, len(embeddings), time.monotonic() - started_at,
    )
    return embeddings


def _chunk_rows(org_id: int, chunks: list[dict], embeddings: list[list[float]]) -> list[tuple]:
    """Build asyncpg parameter tuples for a batch of chunks."""
    return [
        (
            org_id, c["repo_name"], c["file_path"], c["chunk_index"],
            c["chunk_type"], c["content"], c["metadata"], _vector_to_pg(embeddings[idx]),
        )
        for idx, c in enumerate(chunks)
    ]


_INSERT_CHUNK_SQL = """
INSERT INTO repo_chunks (org_id, repo_name, file_path, chunk_index, chunk_type, content, metadata, embedding)
VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::vector)
ON CONFLICT (org_id, repo_name, file_path, chunk_index) DO UPDATE
SET content=EXCLUDED.content, metadata=EXCLUDED.metadata,
    embedding=EXCLUDED.embedding, indexed_at=NOW()
"""

# Registry of in-flight indexing tasks, keyed by (org_id, repo_name). Both API
# servers and the scheduler share one event loop, so a cancel request from the
# REST API can cancel a task started by the scheduler.
_running_index_tasks: dict[tuple[int, str], asyncio.Task] = {}


def is_index_running(org_id: int, repo_name: str) -> bool:
    task = _running_index_tasks.get((org_id, repo_name))
    return task is not None and not task.done()


def cancel_index_task(org_id: int, repo_name: str) -> bool:
    """Request cancellation of a running index task. Returns True if one was running."""
    task = _running_index_tasks.get((org_id, repo_name))
    if task and not task.done():
        task.cancel()
        return True
    return False


async def run_index_repo(
    org_id: int,
    repo: RepoConfig,
    indexing_cfg: IndexingConfig | None = None,
    *,
    force_full: bool = False,
) -> bool:
    """Run ``index_repo`` as a registered, cancellable task.

    Skips (returning False) if the repo is already being indexed, so scheduler
    and manual triggers can't stack concurrent runs of the same repo. Returns
    False when the run was cancelled, True otherwise.
    """
    key = (org_id, repo.name)
    if is_index_running(org_id, repo.name):
        logger.info("Indexing already running for org=%s repo=%s; skipping", org_id, repo.name)
        return False

    task = asyncio.create_task(index_repo(org_id, repo, indexing_cfg, force_full=force_full))
    _running_index_tasks[key] = task
    try:
        await task
        return True
    except asyncio.CancelledError:
        if not task.cancelled():
            raise  # the awaiting coroutine itself was cancelled — propagate
        logger.info("Indexing cancelled for org=%s repo=%s", org_id, repo.name)
        return False
    finally:
        _running_index_tasks.pop(key, None)


async def reset_stale_indexing() -> None:
    """Recover repos left in 'indexing' by a crash or restart.

    Repos that already have chunks fall back to 'indexed' (their data is still
    usable); the rest return to 'pending' so the startup indexer retries them.
    Without this, an interrupted run leaves the repo stuck in 'indexing' forever.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE repos SET status = CASE WHEN total_chunks > 0 THEN 'indexed' ELSE 'pending' END "
            "WHERE status='indexing'"
        )
    count = int(result.split()[-1]) if result else 0
    if count:
        logger.warning("Reset %d repo(s) stuck in 'indexing' from a previous run", count)


async def index_repo(
    org_id: int,
    repo: RepoConfig,
    indexing_cfg: IndexingConfig | None = None,
    *,
    force_full: bool = False,
) -> None:
    """Index a repository for an organization: parse, embed, store chunks.

    When the repo is a git checkout that was previously indexed at a known
    commit, only the files changed since then are re-processed (incremental).
    Otherwise — first index, non-git repo, unavailable base commit, or
    ``force_full`` — the whole tree is re-indexed.
    """
    pool = await get_pool()
    if indexing_cfg is None:
        from ..org_config import get_org_config

        indexing_cfg = (await get_org_config(org_id)).indexing

    async with pool.acquire() as conn:
        prev = await conn.fetchrow(
            "SELECT status, indexed_commit, total_chunks FROM repos WHERE org_id=$1 AND name=$2",
            org_id, repo.name,
        )
        await conn.execute(
            "UPDATE repos SET status='indexing', error_message=NULL WHERE org_id=$1 AND name=$2",
            org_id, repo.name,
        )

    try:
        # Ensure repo is available locally (this pulls remotes to the latest commit).
        if repo.type != "local":
            local_path = await ensure_repo_cloned(repo, org_id)
        else:
            local_path = get_repo_local_path(repo, org_id)

        if not Path(local_path).exists():
            raise FileNotFoundError(f"Repo path does not exist: {local_path}")

        language = repo.language if repo.language != "auto" else None
        is_git = (Path(local_path) / ".git").exists()
        new_commit = await get_head_commit(local_path) if is_git else None

        can_incremental = (
            not force_full
            and is_git
            and new_commit is not None
            and prev is not None
            and prev["status"] == "indexed"
            and prev["indexed_commit"]
            and (prev["total_chunks"] or 0) > 0
            and await commit_exists(local_path, prev["indexed_commit"])
        )

        if can_incremental:
            handled = await _index_repo_incremental(
                pool, org_id, repo, indexing_cfg, language,
                local_path, prev["indexed_commit"], new_commit,
            )
            if handled:
                return
            logger.info("Incremental indexing unavailable for %s; running full index", repo.name)

        await _index_repo_full(pool, org_id, repo, indexing_cfg, language, local_path, new_commit)

    except Exception as e:
        logger.error("Indexing failed for %s: %s", repo.name, e)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE repos SET status='error', error_message=$3 WHERE org_id=$1 AND name=$2",
                org_id, repo.name, str(e),
            )


async def _index_repo_full(
    pool,
    org_id: int,
    repo: RepoConfig,
    indexing_cfg: IndexingConfig,
    language: str | None,
    local_path: str,
    new_commit: str | None,
) -> None:
    """Full re-index: scan the whole tree, replace all of the repo's chunks."""
    loop = asyncio.get_running_loop()
    logger.info("Scanning and parsing files for %s ...", repo.name)
    all_chunks = await loop.run_in_executor(
        None, _collect_chunks_sync, repo.name, local_path, indexing_cfg, language
    )

    if not all_chunks:
        logger.warning("No chunks found for repo %s", repo.name)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE repos SET status='indexed', last_indexed_at=NOW(), total_chunks=0, "
                "indexed_commit=$3 WHERE org_id=$1 AND name=$2",
                org_id, repo.name, new_commit,
            )
        return

    logger.info("Embedding %d chunks for %s", len(all_chunks), repo.name)
    embeddings = await _embed_chunks(all_chunks, repo.name)

    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM repo_chunks WHERE org_id=$1 AND repo_name=$2", org_id, repo.name
        )
        insert_batch_size = 500
        for start in range(0, len(all_chunks), insert_batch_size):
            end = min(start + insert_batch_size, len(all_chunks))
            await conn.executemany(
                _INSERT_CHUNK_SQL,
                _chunk_rows(org_id, all_chunks[start:end], embeddings[start:end]),
            )
            logger.info("DB write progress for %s: inserted=%d/%d", repo.name, end, len(all_chunks))
        await conn.execute(
            """
            UPDATE repos
            SET status='indexed', last_indexed_at=NOW(), total_chunks=$3,
                indexed_commit=$4, error_message=NULL
            WHERE org_id=$1 AND name=$2
            """,
            org_id, repo.name, len(all_chunks), new_commit,
        )
    logger.info("Indexed %d chunks for %s (full)", len(all_chunks), repo.name)


async def _index_repo_incremental(
    pool,
    org_id: int,
    repo: RepoConfig,
    indexing_cfg: IndexingConfig,
    language: str | None,
    local_path: str,
    old_commit: str,
    new_commit: str,
) -> bool:
    """Re-index only files changed between two commits.

    Returns True if the incremental update was applied, or False if the diff
    could not be computed (caller should fall back to a full re-index).
    """
    diff = await get_changed_files(local_path, old_commit, new_commit)
    if diff is None:
        return False
    changed, deleted = diff

    # Every touched path — modified, deleted, or renamed away — has its existing
    # chunks removed; freshly chunked paths are then re-inserted. Deleting first
    # also cleans up files that became non-indexable (grew too large, newly
    # excluded, or converted to a binary type).
    stale_paths = sorted(changed | deleted)

    if not changed and not deleted:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE repos SET status='indexed', last_indexed_at=NOW(), "
                "indexed_commit=$3, error_message=NULL WHERE org_id=$1 AND name=$2",
                org_id, repo.name, new_commit,
            )
        logger.info("No changes for %s since %s; index up to date", repo.name, old_commit[:8])
        return True

    loop = asyncio.get_running_loop()
    chunks, indexed_paths = await loop.run_in_executor(
        None, _collect_changed_chunks_sync, repo.name, local_path, indexing_cfg, language, sorted(changed)
    )

    embeddings = await _embed_chunks(chunks, repo.name) if chunks else []

    async with pool.acquire() as conn:
        async with conn.transaction():
            if stale_paths:
                await conn.execute(
                    "DELETE FROM repo_chunks WHERE org_id=$1 AND repo_name=$2 AND file_path = ANY($3)",
                    org_id, repo.name, stale_paths,
                )
            insert_batch_size = 500
            for start in range(0, len(chunks), insert_batch_size):
                end = min(start + insert_batch_size, len(chunks))
                await conn.executemany(
                    _INSERT_CHUNK_SQL,
                    _chunk_rows(org_id, chunks[start:end], embeddings[start:end]),
                )
            total = await conn.fetchval(
                "SELECT count(*) FROM repo_chunks WHERE org_id=$1 AND repo_name=$2",
                org_id, repo.name,
            )
            await conn.execute(
                """
                UPDATE repos
                SET status='indexed', last_indexed_at=NOW(), total_chunks=$3,
                    indexed_commit=$4, error_message=NULL
                WHERE org_id=$1 AND name=$2
                """,
                org_id, repo.name, total, new_commit,
            )
    logger.info(
        "Indexed %s (incremental): changed=%d deleted=%d reindexed_files=%d new_chunks=%d total=%d",
        repo.name, len(changed), len(deleted), len(indexed_paths), len(chunks), total,
    )
    return True


async def sync_repos_config(org_id: int | None = None) -> None:
    """Sync repos from per-org config into the DB repos table.

    When ``org_id`` is given only that organization is synced; otherwise every
    organization is synced.
    """
    from ..org_config import get_org_config, iter_org_configs

    if org_id is not None:
        configs = [(org_id, await get_org_config(org_id))]
    else:
        configs = await iter_org_configs()

    pool = await get_pool()
    async with pool.acquire() as conn:
        for oid, cfg in configs:
            for repo in cfg.repos:
                await conn.execute(
                    """
                    INSERT INTO repos (org_id, name, type, url, path, branch, language, status)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending')
                    ON CONFLICT (org_id, name) DO UPDATE
                    SET type=EXCLUDED.type, url=EXCLUDED.url, path=EXCLUDED.path,
                        branch=EXCLUDED.branch, language=EXCLUDED.language
                    """,
                    oid,
                    repo.name,
                    repo.type,
                    repo.url,
                    repo.path,
                    repo.branch,
                    repo.language,
                )


async def run_pending_index_requests() -> None:
    """Process index requests queued via the API or repo_index MCP tool."""
    from ..org_config import get_org_config

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, org_id, repo_name FROM index_requests "
            "WHERE processed_at IS NULL ORDER BY requested_at LIMIT 10"
        )

    if not rows:
        return

    for row in rows:
        oid = row["org_id"]
        repo_name = row["repo_name"]
        if oid is None:
            # Legacy request with no org context — skip safely.
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE index_requests SET processed_at=NOW() WHERE id=$1", row["id"]
                )
            continue

        cfg = await get_org_config(oid)
        if repo_name:
            repos_to_index = [r for r in cfg.repos if r.name == repo_name]
        else:
            repos_to_index = cfg.repos

        for repo in repos_to_index:
            logger.info("Processing index request for org=%s repo=%s", oid, repo.name)
            await run_index_repo(oid, repo, cfg.indexing)

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE index_requests SET processed_at=NOW() WHERE id=$1",
                row["id"],
            )
