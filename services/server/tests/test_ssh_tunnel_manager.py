"""Test della logica di ensure_tunnel/close_tunnel con un forwarder finto.

Nessun SSH reale: si sostituisce sshtunnel.SSHTunnelForwarder con un fake che
registra i kwargs ricevuti e simula start/stop, così si verificano il mapping
degli argomenti di auth, il caching per connessione e la chiusura.
"""
import sshtunnel

from src.datasources import engines


class FakeForwarder:
    instances: list["FakeForwarder"] = []

    def __init__(self, ssh_address_or_host, **kwargs):
        self.ssh_address = ssh_address_or_host
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.is_active = False
        self.local_bind_port = 15000 + len(FakeForwarder.instances)
        FakeForwarder.instances.append(self)

    def start(self):
        self.started = True
        self.is_active = True

    def stop(self):
        self.stopped = True
        self.is_active = False


def _patch(monkeypatch):
    FakeForwarder.instances = []
    monkeypatch.setattr(sshtunnel, "SSHTunnelForwarder", FakeForwarder)
    # tunnel cache pulita
    for cid in list(engines._tunnels):
        engines.close_tunnel(cid)


def test_password_auth_maps_kwargs(monkeypatch):
    _patch(monkeypatch)
    cfg = {"host": "bastion", "port": 22, "username": "lascaux",
           "auth_method": "password", "password": "s3cret", "private_key": ""}
    host, port = engines.ensure_tunnel(1, cfg, "10.0.0.5", 5432)

    assert host == "127.0.0.1"
    fwd = FakeForwarder.instances[-1]
    assert fwd.ssh_address == ("bastion", 22)
    assert fwd.kwargs["ssh_username"] == "lascaux"
    assert fwd.kwargs["ssh_password"] == "s3cret"
    assert fwd.kwargs["remote_bind_address"] == ("10.0.0.5", 5432)
    assert fwd.started is True
    assert port == fwd.local_bind_port


def test_key_auth_builds_pkey(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(engines, "_pkey_from_string", lambda pem: f"PKEY({pem[:3]})")
    cfg = {"host": "b", "port": 22, "username": "u", "auth_method": "key",
           "password": "", "private_key": "KEYDATA"}
    engines.ensure_tunnel(2, cfg, "db", 3306)

    fwd = FakeForwarder.instances[-1]
    assert fwd.kwargs["ssh_pkey"] == "PKEY(KEY)"
    assert "ssh_password" not in fwd.kwargs


def test_reuses_tunnel_for_same_fingerprint(monkeypatch):
    _patch(monkeypatch)
    cfg = {"host": "b", "port": 22, "username": "u", "auth_method": "password",
           "password": "p", "private_key": ""}
    engines.ensure_tunnel(3, cfg, "db", 5432)
    engines.ensure_tunnel(3, cfg, "db", 5432)
    # Un solo forwarder creato: il secondo giro riusa il tunnel attivo.
    assert len(FakeForwarder.instances) == 1


def test_rebuilds_tunnel_on_fingerprint_change(monkeypatch):
    _patch(monkeypatch)
    cfg = {"host": "b", "port": 22, "username": "u", "auth_method": "password",
           "password": "p", "private_key": ""}
    engines.ensure_tunnel(4, cfg, "db", 5432)
    engines.ensure_tunnel(4, cfg, "db", 5433)  # remote port cambiato
    assert len(FakeForwarder.instances) == 2
    assert FakeForwarder.instances[0].stopped is True


def test_close_tunnel_stops_and_forgets(monkeypatch):
    _patch(monkeypatch)
    cfg = {"host": "b", "port": 22, "username": "u", "auth_method": "password",
           "password": "p", "private_key": ""}
    engines.ensure_tunnel(5, cfg, "db", 5432)
    engines.close_tunnel(5)
    assert FakeForwarder.instances[-1].stopped is True
    assert 5 not in engines._tunnels
