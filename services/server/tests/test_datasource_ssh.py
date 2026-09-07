import asyncio

from src.datasources import engines, service


def _record(**over):
    base = {
        "id": 7,
        "engine": "postgresql",
        "host": "10.0.0.5",
        "port": 5432,
        "database_name": "app",
        "username": "reader",
        "password_enc": "",
        "options": {},
        "ssh_enabled": False,
    }
    base.update(over)
    return base


def test_resolve_engine_direct_uses_record_host(monkeypatch):
    captured = {}
    monkeypatch.setattr(engines, "get_engine", lambda cid, eng, url: captured.setdefault("url", url))

    asyncio.run(service._resolve_engine(_record()))
    assert captured["url"].host == "10.0.0.5"
    assert captured["url"].port == 5432


def test_resolve_engine_ssh_routes_through_local_forward(monkeypatch):
    calls = {}

    def fake_tunnel(cid, ssh_cfg, remote_host, remote_port):
        calls["cid"] = cid
        calls["remote"] = (remote_host, remote_port)
        calls["ssh_host"] = ssh_cfg["host"]
        return "127.0.0.1", 15999

    captured = {}
    monkeypatch.setattr(engines, "ensure_tunnel", fake_tunnel)
    monkeypatch.setattr(engines, "get_engine", lambda cid, eng, url: captured.setdefault("url", url))

    rec = _record(
        ssh_enabled=True, ssh_host="192.168.2.39", ssh_port=22,
        ssh_username="lascaux", ssh_auth_method="password", ssh_password_enc="",
    )
    asyncio.run(service._resolve_engine(rec))

    # L'engine punta al forward locale, non al DB remoto.
    assert captured["url"].host == "127.0.0.1"
    assert captured["url"].port == 15999
    # Il tunnel è verso il DB remoto attraverso il bastion.
    assert calls["remote"] == ("10.0.0.5", 5432)
    assert calls["ssh_host"] == "192.168.2.39"


def test_dispose_engine_closes_tunnel(monkeypatch):
    stopped = {"v": False}

    class _FakeServer:
        is_active = True

        def stop(self):
            stopped["v"] = True

    engines._tunnels[99] = (_FakeServer(), ("fp",))
    engines.dispose_engine(99)
    assert stopped["v"] is True
    assert 99 not in engines._tunnels


def test_serialize_hides_ssh_secrets_and_flags_presence():
    row = {
        "id": 1, "org_id": 1, "project_id": 1, "name": "db", "engine": "postgresql",
        "host": "h", "port": 5432, "database_name": "d", "username": "u",
        "password_enc": "enc", "options": {}, "description": None,
        "status": "unknown", "error_message": None, "last_checked_at": None,
        "created_at": None, "updated_at": None,
        "ssh_enabled": True, "ssh_host": "b", "ssh_port": 22, "ssh_username": "x",
        "ssh_auth_method": "key", "ssh_password_enc": "", "ssh_private_key_enc": "keyblob",
    }
    out = service._record_to_dict(row)
    assert out["has_ssh_secret"] is True
    assert "ssh_private_key_enc" not in out
    assert "ssh_password_enc" not in out
    assert out["ssh_host"] == "b"


def test_mark_pending_secret_updates_status(monkeypatch):
    import asyncio

    from src.datasources import service as ds_service

    executed = []

    class FakeConn:
        async def execute(self, query, *args):
            executed.append((query, args))

    class FakePool:
        def acquire(self):
            class _Ctx:
                async def __aenter__(self_inner):
                    return FakeConn()

                async def __aexit__(self_inner, *exc):
                    return False

            return _Ctx()

    async def fake_pool():
        return FakePool()

    monkeypatch.setattr(ds_service, "get_pool", fake_pool)
    asyncio.run(ds_service.mark_pending_secret(1, 4, 9))

    query, args = executed[0]
    assert "UPDATE db_connections" in query and "pending_secret" in query
    assert args == (1, 4, 9)
