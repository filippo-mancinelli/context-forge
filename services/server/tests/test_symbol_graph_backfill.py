"""Costruzione del grafo per i repository già indicizzati.

Un repository indicizzato prima che il grafo esistesse resta senza finché non
cambia un commit: l'indicizzazione incrementale, non trovando modifiche, esce
subito. Il giro periodico deve accorgersene e costruirlo una volta sola.
"""
import asyncio

import pytest

from src.indexer import indexer


class _FakeConn:
    def __init__(self, existing):
        self.existing = existing
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return self.existing

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self._conn


def _run(pool, monkeypatch, only_if_missing):
    built = {}

    def _collect(local_path, cfg, language, only_paths):
        built["collected"] = True
        return [{"file_path": "a.py", "name": "Scheduler", "kind": "def", "occurrences": 1}]

    async def _store(*args, **kwargs):
        built["stored"] = True

    monkeypatch.setattr(indexer, "collect_symbols_sync", _collect)
    monkeypatch.setattr(indexer, "_store_symbols", _store)
    asyncio.run(
        indexer._index_symbols(
            pool, 1, 18, "askme-desk", "/tmp/repo", object(), None,
            only_if_missing=only_if_missing,
        )
    )
    return built


def test_graph_is_built_when_the_repo_has_none(monkeypatch):
    conn = _FakeConn(existing=None)
    built = _run(_FakePool(conn), monkeypatch, only_if_missing=True)
    assert built == {"collected": True, "stored": True}
    assert "repo_symbols" in conn.calls[0][0]
    assert conn.calls[0][1] == (18, "askme-desk")


def test_existing_graph_is_not_rebuilt(monkeypatch):
    conn = _FakeConn(existing=1)
    built = _run(_FakePool(conn), monkeypatch, only_if_missing=True)
    assert built == {}


def test_without_the_guard_it_always_rebuilds(monkeypatch):
    conn = _FakeConn(existing=1)
    built = _run(_FakePool(conn), monkeypatch, only_if_missing=False)
    assert built == {"collected": True, "stored": True}
    # Senza guardia non si interroga nemmeno il database.
    assert conn.calls == []


def test_a_failure_does_not_propagate(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("parser esploso")

    monkeypatch.setattr(indexer, "collect_symbols_sync", _boom)
    # L'indicizzazione dei chunk è già andata a buon fine: il grafo che non si
    # costruisce non deve marcare il repository come fallito.
    asyncio.run(
        indexer._index_symbols(
            _FakePool(_FakeConn(existing=None)), 1, 18, "askme-desk", "/tmp/repo",
            object(), None,
        )
    )
