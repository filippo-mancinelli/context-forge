"""Embedding generation.

Supported providers (via EMBEDDINGS_PROVIDER):
  - openai          : OpenAI text-embedding-* models
  - jina            : Jina AI embeddings (1M tokens/month free)
  - openai-compatible: Any OpenAI-compatible endpoint (set EMBEDDINGS_BASE_URL)
  - local           : sentence-transformers (requires EMBEDDINGS_PROVIDER=local at build time)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Sequence

logger = logging.getLogger(__name__)

_openai_client = None
_local_model = None

# Known provider defaults
_PROVIDER_DEFAULTS = {
    "openai": {
        "base_url": None,
        "dims": 1536,
        "model": "text-embedding-3-small",
    },
    "jina": {
        "base_url": "https://api.jina.ai/v1",
        "dims": 1024,
        "model": "jina-embeddings-v3",
    },
}


def _get_api_client():
    """Get (or create) the AsyncOpenAI client for the configured embeddings provider."""
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        from ..config import get_settings
        s = get_settings()

        provider = s.embeddings_provider
        defaults = _PROVIDER_DEFAULTS.get(provider, {})

        # Resolve API key: EMBEDDINGS_API_KEY > provider-specific key > OPENAI_API_KEY
        api_key = (
            s.embeddings_api_key
            or (s.openai_api_key if provider == "openai" else "")
            or s.openai_api_key  # final fallback
        )

        # Resolve base URL: EMBEDDINGS_BASE_URL > provider default
        base_url = s.embeddings_base_url or defaults.get("base_url")

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url

        _openai_client = AsyncOpenAI(**kwargs)
        logger.info(
            "Embeddings client: provider=%s base_url=%s model=%s",
            provider, base_url or "openai-default", s.embeddings_model,
        )
    return _openai_client


def reset_embedder_clients() -> None:
    """Reset provider clients so runtime settings changes are applied."""
    global _openai_client, _local_model
    _openai_client = None
    _local_model = None


def _get_local_model():
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer
        from ..config import get_settings
        model_name = get_settings().embeddings_model or "all-MiniLM-L6-v2"
        logger.info("Loading local embedding model: %s", model_name)
        _local_model = SentenceTransformer(model_name)
    return _local_model


async def embed_text(text: str) -> list[float]:
    """Embed a single text string."""
    results = await embed_batch([text])
    return results[0]


async def embed_batch(texts: Sequence[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns list of embedding vectors."""
    from ..config import get_settings
    settings = get_settings()

    if settings.embeddings_provider == "local":
        return await _embed_local(texts)
    return await _embed_api(texts, settings.embeddings_model)


async def _embed_api(texts: Sequence[str], model: str) -> list[list[float]]:
    """Call any OpenAI-compatible embeddings API."""
    client = _get_api_client()
    batch_size = 20
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = [t[:8000] for t in texts[i:i + batch_size]]
        batch_embeddings = await _embed_api_batch_with_retry(client, model, batch)
        all_embeddings.extend(batch_embeddings)
    return all_embeddings


async def _embed_api_batch_with_retry(client, model: str, batch: list[str]) -> list[list[float]]:
    """Embed a batch with retry/backoff and adaptive split on 429 rate limits."""
    max_retries = 4
    for attempt in range(max_retries + 1):
        try:
            resp = await client.embeddings.create(model=model, input=batch)
            return [item.embedding for item in resp.data]
        except Exception as e:
            msg = str(e)
            is_rate_limited = (
                "429" in msg
                or "rate limit" in msg.lower()
                or "RATE_TOKEN_LIMIT_EXCEEDED" in msg
            )
            if not is_rate_limited:
                raise

            # If provider enforces token/minute, split the batch and retry recursively.
            if len(batch) > 1:
                mid = max(1, len(batch) // 2)
                left = await _embed_api_batch_with_retry(client, model, batch[:mid])
                right = await _embed_api_batch_with_retry(client, model, batch[mid:])
                return left + right

            if attempt >= max_retries:
                raise

            wait_seconds = min(2 ** attempt, 30)
            logger.warning(
                "Embeddings 429 for single input. Retrying in %ss (attempt %d/%d)",
                wait_seconds,
                attempt + 1,
                max_retries + 1,
            )
            await asyncio.sleep(wait_seconds)

    # Defensive fallback, should be unreachable.
    raise RuntimeError("Embeddings retry loop exhausted unexpectedly")


async def _embed_local(texts: Sequence[str]) -> list[list[float]]:
    loop = asyncio.get_event_loop()
    model = _get_local_model()
    embeddings = await loop.run_in_executor(
        None, lambda: model.encode(list(texts), batch_size=32, show_progress_bar=False)
    )
    return [e.tolist() for e in embeddings]
