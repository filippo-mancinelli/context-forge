import asyncio

import pytest

from src import repo_registry


class FakeConn:
    def __init__(self):
        self.executed = []

    async def execute(self, query, *args):
        self.executed.append((query, args))


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


class FakeConfig:
    def __init__(self, repos=None):
        self.repos = repos or []


def _patch(monkeypatch, conn, cfg):
    persisted = []
    synced = []

    async def fake_pool():
        return FakePool(conn)

    async def fake_get_config(org_id):
        return cfg

    async def fake_persist(org_id, config):
        persisted.append((org_id, config))

    async def fake_sync(org_id):
        synced.append(org_id)

    monkeypatch.setattr(repo_registry, "get_pool", fake_pool)
    monkeypatch.setattr(repo_registry, "get_org_config", fake_get_config)
    monkeypatch.setattr(repo_registry, "persist_org_config", fake_persist)
    monkeypatch.setattr(repo_registry, "sync_repos_config", fake_sync)
    return persisted, synced


def test_add_repo_appends_to_config_and_binds_the_project(monkeypatch):
    conn = FakeConn()
    cfg = FakeConfig()
    persisted, synced = _patch(monkeypatch, conn, cfg)

    out = asyncio.run(
        repo_registry.add_repo(
            org_id=1,
            project_id=4,
            name="askmeai-v2",
            type="gitlab",
            url="https://git.lascaux.it/askme/askmeai-v2",
            branch="develop",
        )
    )

    assert out["name"] == "askmeai-v2"
    assert [r.name for r in cfg.repos] == ["askmeai-v2"]
    assert cfg.repos[0].branch == "develop"
    # language non passato -> 'auto', come fa la route
    assert cfg.repos[0].language == "auto"
    assert persisted and synced == [1]
    # il repo appena creato viene legato al progetto attivo
    assert any("UPDATE repos SET project_id" in q for q, _ in conn.executed)


def test_add_repo_rejects_a_duplicate_name(monkeypatch):
    conn = FakeConn()
    cfg = FakeConfig(repos=[type("R", (), {"name": "askmeai-v2"})()])
    persisted, synced = _patch(monkeypatch, conn, cfg)

    with pytest.raises(repo_registry.RepoAlreadyExistsError):
        asyncio.run(
            repo_registry.add_repo(org_id=1, project_id=4, name="askmeai-v2", type="gitlab")
        )

    assert persisted == [] and synced == []


def test_queue_index_inserts_a_request(monkeypatch):
    conn = FakeConn()
    cfg = FakeConfig()
    _patch(monkeypatch, conn, cfg)

    asyncio.run(repo_registry.queue_index(1, 4, "askmeai-v2"))

    query, args = conn.executed[-1]
    assert "INSERT INTO index_requests" in query
    assert args == (1, 4, "askmeai-v2")
