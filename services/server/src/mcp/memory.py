"""MCP tools for persistent memory via Mem0."""
from __future__ import annotations

import logging
from typing import Any, Optional

from .server import mcp

logger = logging.getLogger(__name__)

_memory_client = None


def _get_memory():
    global _memory_client
    if _memory_client is None:
        from ..config import get_settings
        from mem0 import Memory

        settings = get_settings()

        llm_config: dict[str, Any] = {
            "provider": settings.llm_provider if settings.llm_provider != "deepseek" else "openai",
            "config": {"model": settings.llm_model},
        }
        if settings.llm_provider == "openai":
            llm_config["config"]["api_key"] = settings.openai_api_key
        elif settings.llm_provider == "anthropic":
            llm_config["config"]["api_key"] = settings.anthropic_api_key
        elif settings.llm_provider == "deepseek":
            # DeepSeek is OpenAI-compatible — use openai provider with custom base URL
            llm_config["config"]["api_key"] = settings.deepseek_api_key
            llm_config["config"]["openai_base_url"] = "https://api.deepseek.com"

        # Resolve embeddings API key: EMBEDDINGS_API_KEY > OPENAI_API_KEY
        emb_api_key = settings.embeddings_api_key or settings.openai_api_key

        # Jina and other OpenAI-compatible providers use the "openai" Mem0 embedder
        # with a custom base_url
        _JINA_BASE = "https://api.jina.ai/v1"
        emb_provider_map = {
            "openai": "openai",
            "jina": "openai",
            "openai-compatible": "openai",
            "local": "huggingface",
        }
        embedder_config: dict[str, Any] = {
            "provider": emb_provider_map.get(settings.embeddings_provider, "openai"),
            "config": {
                "model": settings.embeddings_model,
                "api_key": emb_api_key,
                "embedding_dims": settings.embeddings_dims,
            },
        }
        base_url = settings.embeddings_base_url
        if not base_url and settings.embeddings_provider == "jina":
            base_url = _JINA_BASE
        if base_url:
            embedder_config["config"]["openai_base_url"] = base_url

        # Parse DB URL for Mem0 pgvector config
        import re
        m = re.match(
            r"postgresql://([^:]+):([^@]+)@([^:/]+):?(\d+)?/(.+)",
            settings.database_url,
        )
        if not m:
            raise ValueError(f"Cannot parse DATABASE_URL: {settings.database_url}")
        db_user, db_pass, db_host, db_port, db_name = m.groups()

        config = {
            "vector_store": {
                "provider": "pgvector",
                "config": {
                    "host": db_host,
                    "port": int(db_port or 5432),
                    "dbname": db_name,
                    "user": db_user,
                    "password": db_pass,
                    "embedding_model_dims": settings.embeddings_dims,
                    "collection_name": "cf_memories",
                },
            },
            "llm": llm_config,
            "embedder": embedder_config,
        }
        _memory_client = Memory.from_config(config)
    return _memory_client


@mcp.tool()
async def memory_add(content: str, metadata: Optional[dict] = None, user_id: Optional[str] = None) -> dict:
    """Save a memory, fact, or note that should persist across sessions.

    Use this to store important decisions, context, preferences, architectural notes,
    or anything that should be remembered for future conversations.

    Args:
        content: The text to remember (a fact, decision, note, etc.)
        metadata: Optional key-value metadata tags (e.g. {"project": "backend", "type": "decision"})
        user_id: User or agent identifier (defaults to the configured default)

    Returns:
        dict with the created memory id and content
    """
    from ..config import get_forge_config
    uid = user_id or get_forge_config().memory.user_id
    try:
        result = _get_memory().add(content, user_id=uid, metadata=metadata or {})
        return {"status": "ok", "memory": result}
    except Exception as e:
        logger.error("memory_add failed: %s", e)
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def memory_search(query: str, limit: int = 10, user_id: Optional[str] = None) -> dict:
    """Search memories semantically.

    Find previously stored memories, decisions, or notes related to the query.

    Args:
        query: Natural language search query
        limit: Maximum number of results to return (default 10)
        user_id: User or agent identifier (defaults to the configured default)

    Returns:
        dict with list of matching memories, each with id, content, score, and metadata
    """
    from ..config import get_forge_config
    uid = user_id or get_forge_config().memory.user_id
    try:
        results = _get_memory().search(query, user_id=uid, limit=limit)
        memories = results.get("results", results) if isinstance(results, dict) else results
        return {"status": "ok", "memories": memories, "count": len(memories)}
    except Exception as e:
        logger.error("memory_search failed: %s", e)
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def memory_list(limit: int = 20, user_id: Optional[str] = None) -> dict:
    """List recent memories.

    Args:
        limit: Maximum number of memories to return (default 20)
        user_id: User or agent identifier (defaults to the configured default)

    Returns:
        dict with list of memories
    """
    from ..config import get_forge_config
    uid = user_id or get_forge_config().memory.user_id
    try:
        results = _get_memory().get_all(user_id=uid)
        memories = results.get("results", results) if isinstance(results, dict) else results
        return {"status": "ok", "memories": memories[:limit], "count": len(memories[:limit])}
    except Exception as e:
        logger.error("memory_list failed: %s", e)
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def memory_delete(memory_id: str) -> dict:
    """Delete a specific memory by its ID.

    Args:
        memory_id: The ID of the memory to delete (from memory_search or memory_list results)

    Returns:
        dict with status
    """
    try:
        _get_memory().delete(memory_id)
        return {"status": "ok", "deleted": memory_id}
    except Exception as e:
        logger.error("memory_delete failed: %s", e)
        return {"status": "error", "error": str(e)}
