"""Embedding generation — supports OpenAI and local sentence-transformers."""
from __future__ import annotations

import asyncio
import logging
from typing import Sequence

logger = logging.getLogger(__name__)

_local_model = None
_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        from ..config import get_settings
        _openai_client = AsyncOpenAI(api_key=get_settings().openai_api_key)
    return _openai_client


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

    if settings.embeddings_provider == "openai":
        return await _embed_openai(texts, settings.embeddings_model)
    else:
        return await _embed_local(texts)


async def _embed_openai(texts: Sequence[str], model: str) -> list[list[float]]:
    client = _get_openai()
    # OpenAI API: max 2048 inputs per request, max 8192 tokens per text
    batch_size = 100
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = list(texts[i:i + batch_size])
        # Truncate very long texts (openai limit)
        batch = [t[:8000] for t in batch]
        resp = await client.embeddings.create(model=model, input=batch)
        all_embeddings.extend([item.embedding for item in resp.data])
    return all_embeddings


async def _embed_local(texts: Sequence[str]) -> list[list[float]]:
    loop = asyncio.get_event_loop()
    model = _get_local_model()
    embeddings = await loop.run_in_executor(
        None, lambda: model.encode(list(texts), batch_size=32, show_progress_bar=False)
    )
    return [e.tolist() for e in embeddings]
