import asyncio

import pytest

from src.kb import fetch


def _resolves_to(monkeypatch, mapping):
    """Sostituisce la risoluzione DNS: host -> indirizzo IP."""

    def fake_resolve(host):
        return mapping[host]

    monkeypatch.setattr(fetch, "_resolve_host", fake_resolve)


def _patch_stream(monkeypatch, response):
    """Sostituisce httpx.AsyncClient.stream con una risposta finta."""

    class _StreamCtx:
        async def __aenter__(self):
            return response

        async def __aexit__(self, *exc):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url):
            return _StreamCtx()

    monkeypatch.setattr(fetch.httpx, "AsyncClient", FakeClient)


def test_only_http_and_https_are_accepted(monkeypatch):
    _resolves_to(monkeypatch, {"example.com": "93.184.216.34"})
    fetch.assert_public_url("https://example.com/a.pdf")
    with pytest.raises(fetch.FetchError):
        fetch.assert_public_url("file:///etc/passwd")
    with pytest.raises(fetch.FetchError):
        fetch.assert_public_url("ftp://example.com/a.pdf")


def test_private_and_loopback_addresses_are_rejected(monkeypatch):
    _resolves_to(
        monkeypatch,
        {
            "internal.lascaux.it": "192.168.2.39",
            "localhost": "127.0.0.1",
            "meta": "169.254.169.254",
            "example.com": "93.184.216.34",
        },
    )
    for host in ("internal.lascaux.it", "localhost", "meta"):
        with pytest.raises(fetch.FetchError):
            fetch.assert_public_url(f"http://{host}/a.pdf")
    fetch.assert_public_url("http://example.com/a.pdf")


def test_filename_prefers_content_disposition():
    assert fetch.filename_from(
        "https://example.com/download?id=7",
        'attachment; filename="manuale.pdf"',
    ) == "manuale.pdf"
    assert fetch.filename_from("https://example.com/docs/manuale.pdf", None) == "manuale.pdf"


def test_unsupported_extension_is_rejected(monkeypatch):
    _resolves_to(monkeypatch, {"example.com": "93.184.216.34"})

    class FakeResponse:
        status_code = 200
        headers = {"content-disposition": 'attachment; filename="script.exe"'}
        url = "https://example.com/script.exe"

        async def aiter_bytes(self, chunk_size=65536):
            yield b"MZ"

        def raise_for_status(self):
            return None

    _patch_stream(monkeypatch, FakeResponse())

    with pytest.raises(fetch.FetchError, match="not supported"):
        asyncio.run(fetch.fetch_document("https://example.com/script.exe"))


def test_download_stops_over_the_size_limit(monkeypatch):
    _resolves_to(monkeypatch, {"example.com": "93.184.216.34"})

    class FakeResponse:
        status_code = 200
        headers = {}
        url = "https://example.com/big.pdf"

        async def aiter_bytes(self, chunk_size=65536):
            # due chunk oltre il limite abbassato dal test
            yield b"x" * 600
            yield b"x" * 600

        def raise_for_status(self):
            return None

    monkeypatch.setattr(fetch, "MAX_FETCH_BYTES", 1000)
    _patch_stream(monkeypatch, FakeResponse())

    with pytest.raises(fetch.FetchError, match="too large"):
        asyncio.run(fetch.fetch_document("https://example.com/big.pdf"))


def test_successful_fetch_returns_filename_and_bytes(monkeypatch):
    _resolves_to(monkeypatch, {"example.com": "93.184.216.34"})

    class FakeResponse:
        status_code = 200
        headers = {}
        url = "https://example.com/docs/manuale.pdf"

        async def aiter_bytes(self, chunk_size=65536):
            yield b"%PDF-1.4 "
            yield b"body"

        def raise_for_status(self):
            return None

    _patch_stream(monkeypatch, FakeResponse())

    filename, data = asyncio.run(fetch.fetch_document("https://example.com/docs/manuale.pdf"))
    assert filename == "manuale.pdf"
    assert data == b"%PDF-1.4 body"
