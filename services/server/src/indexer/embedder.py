"""Embedding generation.

Supported providers (via EMBEDDINGS_PROVIDER):
  - openai          : OpenAI text-embedding-* models
  - jina            : Jina AI embeddings (1M tokens/month free)
  - openai-compatible: Any OpenAI-compatible endpoint (set EMBEDDINGS_BASE_URL)
  - local           : sentence-transformers (requires EMBEDDINGS_PROVIDER=local at build time)

Each organization can override its embeddings provider/model/key/base URL
(see ``org_settings``). API clients are cached per resolved configuration
signature ``(provider, base_url, api_key, model)`` so organizations sharing
the same configuration share a client, while distinct configurations get
their own.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from ..org_settings import OrgSettings, get_org_settings

logger = logging.getLogger(__name__)

# Client per firma di configurazione (provider, base_url, api_key, model):
# org diverse con la stessa config condividono il client.
_api_clients: dict[tuple, object] = {}
_org_client_keys: dict[int, tuple] = {}
_local_models: dict[str, object] = {}

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


def _make_client(api_key, base_url):
    from openai import AsyncOpenAI

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


def _resolve_client_config(s: OrgSettings) -> tuple:
    defaults = _PROVIDER_DEFAULTS.get(s.embeddings_provider, {})
    api_key = s.embeddings_api_key or s.openai_api_key
    base_url = s.embeddings_base_url or defaults.get("base_url")
    return (s.embeddings_provider, base_url, api_key, s.embeddings_model)


async def _get_api_client(org_id: int):
    """Get (or create) the AsyncOpenAI client for an organization's embeddings config."""
    s = await get_org_settings(org_id)
    key = _resolve_client_config(s)
    client = _api_clients.get(key)
    if client is None:
        client = _make_client(key[2], key[1])
        _api_clients[key] = client
        logger.info(
            "Embeddings client: provider=%s base_url=%s model=%s (org=%s)",
            key[0], key[1] or "openai-default", key[3], org_id,
        )
    _org_client_keys[org_id] = key
    return client


def reset_embedder_clients(org_id: int | None = None) -> None:
    """Reset provider clients so settings changes are applied."""
    global _local_models
    if org_id is None:
        _api_clients.clear()
        _org_client_keys.clear()
        _local_models = {}
        return
    key = _org_client_keys.pop(org_id, None)
    if key is not None:
        _api_clients.pop(key, None)


def _get_local_model(model_name: str):
    model = _local_models.get(model_name)
    if model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading local embedding model: %s", model_name)
        model = SentenceTransformer(model_name)
        _local_models[model_name] = model
    return model


async def embed_text(text: str, org_id: int) -> list[float]:
    """Embed a single text string with the organization's embedder."""
    results = await embed_batch([text], org_id)
    return results[0]


async def embed_batch(texts: Sequence[str], org_id: int) -> list[list[float]]:
    """Embed a batch of texts with the organization's embedder."""
    s = await get_org_settings(org_id)
    if s.embeddings_provider == "local":
        return await _embed_local(texts, s.embeddings_model or "all-MiniLM-L6-v2")
    client = await _get_api_client(org_id)
    return await _embed_api(client, texts, s.embeddings_model)


async def _embed_api(client, texts: Sequence[str], model: str) -> list[list[float]]:
    """Call any OpenAI-compatible embeddings API."""
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


async def _embed_local(texts: Sequence[str], model_name: str) -> list[list[float]]:
    loop = asyncio.get_event_loop()
    model = _get_local_model(model_name)
    embeddings = await loop.run_in_executor(
        None, lambda: model.encode(list(texts), batch_size=32, show_progress_bar=False)
    )
    return [e.tolist() for e in embeddings]
