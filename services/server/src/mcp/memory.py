"""MCP tools for persistent memory via Mem0."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .server import mcp
from .permissions import requires_permission

logger = logging.getLogger(__name__)

_memory_clients: dict[int, object] = {}

_EMBEDDING_FIELDS = (
    "embeddings_provider", "embeddings_model", "embeddings_dims",
    "embeddings_api_key", "embeddings_base_url",
)


def _memory_collection_name(org_id, s, bootstrap) -> str:
    """Collection pgvector per le memorie dell'org: quella condivisa finché la
    config embeddings coincide col bootstrap (i dati esistenti restano
    leggibili), dedicata quando l'org diverge."""
    if all(getattr(s, f) == getattr(bootstrap, f) for f in _EMBEDDING_FIELDS):
        return "cf_memories"
    return f"cf_memories_org{org_id}"


def _build_memory_config(s, database_url: str, collection_name: str) -> dict:
    llm_config = {
        "provider": s.llm_provider if s.llm_provider != "deepseek" else "openai",
        "config": {"model": s.llm_model},
    }
    if s.llm_provider == "openai":
        llm_config["config"]["api_key"] = s.openai_api_key
    elif s.llm_provider == "anthropic":
        llm_config["config"]["api_key"] = s.anthropic_api_key
    elif s.llm_provider == "deepseek":
        # DeepSeek is OpenAI-compatible — use openai provider with custom base URL
        llm_config["config"]["api_key"] = s.deepseek_api_key
        llm_config["config"]["openai_base_url"] = "https://api.deepseek.com"

    # Resolve embeddings API key: EMBEDDINGS_API_KEY > OPENAI_API_KEY
    emb_api_key = s.embeddings_api_key or s.openai_api_key
    # Jina and other OpenAI-compatible providers use the "openai" Mem0 embedder
    # with a custom base_url
    _JINA_BASE = "https://api.jina.ai/v1"
    emb_provider_map = {
        "openai": "openai",
        "jina": "openai",
        "openai-compatible": "openai",
        "local": "huggingface",
    }
    embedder_config = {
        "provider": emb_provider_map.get(s.embeddings_provider, "openai"),
        "config": {
            "model": s.embeddings_model,
            "api_key": emb_api_key,
            "embedding_dims": s.embeddings_dims,
        },
    }
    base_url = s.embeddings_base_url
    if not base_url and s.embeddings_provider == "jina":
        base_url = _JINA_BASE
    if base_url:
        embedder_config["config"]["openai_base_url"] = base_url

    # Parse DB URL for Mem0 pgvector config
    import re
    m = re.match(r"postgresql://([^:]+):([^@]+)@([^:/]+):?(\d+)?/(.+)", database_url)
    if not m:
        raise ValueError(f"Cannot parse DATABASE_URL: {database_url}")
    db_user, db_pass, db_host, db_port, db_name = m.groups()

    return {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "host": db_host,
                "port": int(db_port or 5432),
                "dbname": db_name,
                "user": db_user,
                "password": db_pass,
                "embedding_model_dims": s.embeddings_dims,
                "collection_name": collection_name,
            },
        },
        "llm": llm_config,
        "embedder": embedder_config,
    }


async def _get_memory(org_id: int):
    client = _memory_clients.get(org_id)
    if client is None:
        from mem0 import Memory

        from ..config import get_settings
        from ..org_settings import ORG_OVERRIDE_FIELDS, OrgSettings, get_org_settings

        s = await get_org_settings(org_id)
        base = get_settings()
        bootstrap = OrgSettings(
            **{f: getattr(base, f) for f in ORG_OVERRIDE_FIELDS}
        )
        collection = _memory_collection_name(org_id, s, bootstrap)
        config = _build_memory_config(s, get_settings().database_url, collection)
        client = Memory.from_config(config)
        _memory_clients[org_id] = client
    return client


def reset_memory_client(org_id: int | None = None) -> None:
    """Reset Mem0 client(s) to apply runtime settings changes."""
    if org_id is None:
        _memory_clients.clear()
    else:
        _memory_clients.pop(org_id, None)


def _normalize_metadata(metadata: dict[str, Any] | str | None) -> dict[str, Any]:
    """Accept metadata as a dict or JSON string and normalize it for Mem0."""
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        parsed = json.loads(metadata)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("metadata JSON string must decode to an object")
    raise ValueError("metadata must be a dictionary or a JSON object string")


@mcp.tool()
@requires_permission("context-write")
async def memory_add(
    content: str,
    metadata: Optional[dict[str, Any] | str] = None,
    infer: bool = False,
) -> dict:
    """Save a memory, fact, or note that should persist across sessions.

    Use this to store important decisions, context, preferences, architectural notes,
    or anything that should be remembered for future conversations.

    Args:
        content: The text to remember (a fact, decision, note, etc.)
        metadata: Optional key-value metadata tags as a dict or JSON object string
            (e.g. {"project": "backend", "type": "decision"})
        infer: If True, runs LLM-based fact extraction that may split content into
            several smaller atomic memories. Defaults to False, which stores content
            verbatim as a single memory — use this when saving a block of instructions,
            a golden-standard note, or anything that should stay intact.

    Returns:
        dict with the created memory id and content
    """
    from ..config import get_forge_config
    from .context import resolve_memory_namespace, resolve_org_id
    uid = await resolve_memory_namespace() or get_forge_config().memory.user_id
    org_id = await resolve_org_id()
    try:
        memory = await _get_memory(org_id)
        result = memory.add(
            content, user_id=uid, metadata=_normalize_metadata(metadata), infer=infer
        )
        return {"status": "ok", "memory": result}
    except Exception as e:
        logger.error("memory_add failed: %s", e)
        return {"status": "error", "error": str(e)}


@mcp.tool()
@requires_permission("context-read")
async def memory_search(query: str, limit: int = 10) -> dict:
    """Search the organization's persistent cross-session memory semantically.

    Find previously stored memories, decisions, or notes related to the query.
    Use this BEFORE answering questions about past decisions, conventions, or
    anything the user may have asked to remember in earlier sessions.

    Args:
        query: Natural language search query
        limit: Maximum number of results to return (default 10)

    Returns:
        dict with list of matching memories, each with id, content, score, and metadata
    """
    from ..config import get_forge_config
    from .context import resolve_memory_namespace, resolve_org_id
    uid = await resolve_memory_namespace() or get_forge_config().memory.user_id
    org_id = await resolve_org_id()
    try:
        memory = await _get_memory(org_id)
        results = memory.search(query, user_id=uid, limit=limit)
        memories = results.get("results", results) if isinstance(results, dict) else results
        return {"status": "ok", "memories": memories, "count": len(memories)}
    except Exception as e:
        logger.error("memory_search failed: %s", e)
        return {"status": "error", "error": str(e)}


@mcp.tool()
@requires_permission("context-read")
async def memory_list(limit: int = 20) -> dict:
    """List recent memories.

    Args:
        limit: Maximum number of memories to return (default 20)

    Returns:
        dict with list of memories
    """
    from ..config import get_forge_config
    from .context import resolve_memory_namespace, resolve_org_id
    uid = await resolve_memory_namespace() or get_forge_config().memory.user_id
    org_id = await resolve_org_id()
    try:
        memory = await _get_memory(org_id)
        results = memory.get_all(user_id=uid)
        memories = results.get("results", results) if isinstance(results, dict) else results
        return {"status": "ok", "memories": memories[:limit], "count": len(memories[:limit])}
    except Exception as e:
        logger.error("memory_list failed: %s", e)
        return {"status": "error", "error": str(e)}


@mcp.tool()
@requires_permission("context-write")
async def memory_delete(memory_id: str) -> dict:
    """Delete a specific memory by its ID.

    Only deletes the memory if it belongs to the current namespace — mem0's
    delete-by-id does not filter by user_id, so the ownership check has to
    happen here before the delete is issued.

    Args:
        memory_id: The ID of the memory to delete (from memory_search or memory_list results)

    Returns:
        dict with status
    """
    from ..config import get_forge_config
    from .context import resolve_memory_namespace, resolve_org_id
    from .permissions import ToolError

    uid = await resolve_memory_namespace() or get_forge_config().memory.user_id
    org_id = await resolve_org_id()

    try:
        memory = await _get_memory(org_id)
        existing = memory.get(memory_id)
    except Exception as e:
        logger.error("memory_delete lookup failed: %s", e)
        return {"status": "error", "error": str(e)}

    if not existing or existing.get("user_id") != uid:
        raise ToolError(f"Memory '{memory_id}' not found in the current namespace")

    try:
        memory.delete(memory_id)
        return {"status": "ok", "deleted": memory_id}
    except Exception as e:
        logger.error("memory_delete failed: %s", e)
        return {"status": "error", "error": str(e)}
