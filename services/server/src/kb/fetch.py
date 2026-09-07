"""Download di un documento da URL per la knowledge base.

È l'unico punto in cui il server esce in rete verso un indirizzo scelto da chi
chiama un tool MCP. Senza vincoli sarebbe un SSRF: un URL su un indirizzo
interno userebbe il server come proxy verso la rete privata, e il contenuto
finirebbe leggibile in knowledge base. Per questo l'host viene risolto e
controllato prima della richiesta e di nuovo sull'URL finale dopo i redirect.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx

from .extract import is_supported

MAX_FETCH_BYTES = 100 * 1024 * 1024
FETCH_TIMEOUT = 30
MAX_REDIRECTS = 3
ALLOWED_SCHEMES = ("http", "https")


class FetchError(Exception):
    """URL non ammesso o download non riuscito."""


def _resolve_host(host: str) -> str:
    """Primo indirizzo IP dell'host. Isolato per poterlo sostituire nei test."""
    return socket.getaddrinfo(host, None)[0][4][0]


def assert_public_url(url: str) -> None:
    """Accetta solo http/https verso un indirizzo pubblico."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise FetchError(f"Only http and https URLs are allowed, got '{parsed.scheme}'")
    if not parsed.hostname:
        raise FetchError("URL has no host")
    try:
        address = ipaddress.ip_address(_resolve_host(parsed.hostname))
    except (KeyError, OSError, ValueError) as exc:
        raise FetchError(f"Cannot resolve host '{parsed.hostname}'") from exc
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    ):
        raise FetchError(
            f"Host '{parsed.hostname}' resolves to a non-public address; refusing to fetch"
        )


def filename_from(url: str, content_disposition: Optional[str]) -> str:
    """Nome file dall'header Content-Disposition, altrimenti dal path dell'URL."""
    if content_disposition:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition)
        if match:
            return unquote(match.group(1).strip())
    name = Path(unquote(urlparse(url).path)).name
    return name or "download"


async def fetch_document(url: str) -> tuple[str, bytes]:
    """Scarica il documento e ritorna (nome file, byte)."""
    assert_public_url(url)
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT, follow_redirects=True, max_redirects=MAX_REDIRECTS
    ) as client:
        async with client.stream("GET", url) as response:
            # Con i redirect seguiti da httpx l'URL finale può puntare altrove:
            # ricontrollarlo, altrimenti il primo controllo si aggira con un 302.
            assert_public_url(str(response.url))
            response.raise_for_status()
            filename = filename_from(
                str(response.url), response.headers.get("content-disposition")
            )
            if not is_supported(filename):
                raise FetchError(f"File type of '{filename}' is not supported")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > MAX_FETCH_BYTES:
                    raise FetchError(
                        f"Document is too large (over {MAX_FETCH_BYTES // (1024 * 1024)} MB)"
                    )
                chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise FetchError("Downloaded document is empty")
    return filename, data
