"""Repository indexer — tree-sitter parsing + pgvector storage."""
from __future__ import annotations

import fnmatch
import json
import logging
import re
from pathlib import Path
from typing import Iterator

from ..config import IndexingConfig, RepoConfig, get_forge_config, get_settings
from ..db import get_pool
from .embedder import embed_batch
from .git_manager import ensure_repo_cloned, get_repo_local_path

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


def _iter_repo_files(repo_path: str, config: IndexingConfig) -> Iterator[tuple[Path, str]]:
    """Yield (absolute_path, relative_path) for all indexable files in a repo."""
    root = Path(repo_path)
    max_size = config.max_file_size_kb * 1024
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        rel = str(file_path.relative_to(root))
        if _should_exclude(rel, config.exclude):
            continue
        suffix = file_path.suffix.lower()
        if suffix in BINARY_EXTENSIONS:
            continue
        if file_path.stat().st_size > max_size:
            continue
        if suffix not in PARSEABLE_EXTENSIONS and suffix not in TEXT_EXTENSIONS:
            continue
        yield file_path, rel


async def index_repo(repo: RepoConfig) -> None:
    """Index a repository: parse, embed, and store chunks in pgvector."""
    pool = await get_pool()
    cfg = get_forge_config()
    indexing_cfg = cfg.indexing
    settings = get_settings()

    # Update status to indexing
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE repos SET status='indexing', error_message=NULL WHERE name=$1",
            repo.name,
        )

    try:
        # Ensure repo is available locally
        if repo.type != "local":
            local_path = await ensure_repo_cloned(repo)
        else:
            local_path = get_repo_local_path(repo)

        if not Path(local_path).exists():
            raise FileNotFoundError(f"Repo path does not exist: {local_path}")

        # Collect files and their chunks
        all_chunks: list[dict] = []
        language = repo.language if repo.language != "auto" else None

        for file_path, rel_path in _iter_repo_files(local_path, indexing_cfg):
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            detected_lang = language or _detect_language(file_path)
            suffix = file_path.suffix.lower()

            if suffix in PARSEABLE_EXTENSIONS:
                ts_chunks = _extract_chunks_treesitter(content, detected_lang, indexing_cfg)
                if ts_chunks:
                    for i, c in enumerate(ts_chunks):
                        all_chunks.append({
                            "repo_name": repo.name,
                            "file_path": rel_path,
                            "chunk_index": i,
                            "chunk_type": c["type"],
                            "content": c["content"],
                            "metadata": json.dumps({"name": c.get("name"), "start_line": c.get("start_line")}),
                        })
                    continue

            # Fallback: sliding window chunking
            sw_chunks = _sliding_window_chunks(content, indexing_cfg.chunk_size, indexing_cfg.chunk_overlap)
            for i, c in enumerate(sw_chunks):
                all_chunks.append({
                    "repo_name": repo.name,
                    "file_path": rel_path,
                    "chunk_index": i,
                    "chunk_type": c["type"],
                    "content": c["content"],
                    "metadata": json.dumps({"start_line": c.get("start_line", 0)}),
                })

        if not all_chunks:
            logger.warning("No chunks found for repo %s", repo.name)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE repos SET status='indexed', last_indexed_at=NOW(), total_chunks=0 WHERE name=$1",
                    repo.name,
                )
            return

        # Embed in batches
        logger.info("Embedding %d chunks for %s", len(all_chunks), repo.name)
        texts = [c["content"] for c in all_chunks]
        batch_size = 50
        embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = await embed_batch(batch)
            embeddings.extend(batch_embeddings)

        # Upsert into DB (delete old chunks for this repo first)
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM repo_chunks WHERE repo_name=$1", repo.name)
            await conn.executemany(
                """
                INSERT INTO repo_chunks (repo_name, file_path, chunk_index, chunk_type, content, metadata, embedding)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::vector)
                ON CONFLICT (repo_name, file_path, chunk_index) DO UPDATE
                SET content=EXCLUDED.content, metadata=EXCLUDED.metadata,
                    embedding=EXCLUDED.embedding, indexed_at=NOW()
                """,
                [
                    (
                        c["repo_name"], c["file_path"], c["chunk_index"],
                        c["chunk_type"], c["content"], c["metadata"],
                        str(embeddings[i]),
                    )
                    for i, c in enumerate(all_chunks)
                ],
            )
            await conn.execute(
                """
                UPDATE repos
                SET status='indexed', last_indexed_at=NOW(), total_chunks=$2, error_message=NULL
                WHERE name=$1
                """,
                repo.name,
                len(all_chunks),
            )
        logger.info("Indexed %d chunks for %s", len(all_chunks), repo.name)

    except Exception as e:
        logger.error("Indexing failed for %s: %s", repo.name, e)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE repos SET status='error', error_message=$2 WHERE name=$1",
                repo.name,
                str(e),
            )


async def sync_repos_config() -> None:
    """Sync repos from config into the DB repos table."""
    cfg = get_forge_config()
    pool = await get_pool()
    async with pool.acquire() as conn:
        for repo in cfg.repos:
            await conn.execute(
                """
                INSERT INTO repos (name, type, url, path, branch, language, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending')
                ON CONFLICT (name) DO UPDATE
                SET type=EXCLUDED.type, url=EXCLUDED.url, path=EXCLUDED.path,
                    branch=EXCLUDED.branch, language=EXCLUDED.language
                """,
                repo.name,
                repo.type,
                repo.url,
                repo.path,
                repo.branch,
                repo.language,
            )


async def run_pending_index_requests() -> None:
    """Process index requests queued via the API or repo_index MCP tool."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, repo_name FROM index_requests WHERE processed_at IS NULL ORDER BY requested_at LIMIT 10"
        )

    if not rows:
        return

    cfg = get_forge_config()
    for row in rows:
        repo_name = row["repo_name"]
        if repo_name:
            repos_to_index = [r for r in cfg.repos if r.name == repo_name]
        else:
            repos_to_index = cfg.repos

        for repo in repos_to_index:
            logger.info("Processing index request for %s", repo.name)
            await index_repo(repo)

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE index_requests SET processed_at=NOW() WHERE id=$1",
                row["id"],
            )
