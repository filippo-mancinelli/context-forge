"""SQLAlchemy engine construction and caching for external database connections.

Engines are synchronous (executed via ``asyncio.to_thread`` by the service
layer) and cached per connection id; the cache entry is invalidated whenever
the connection's URL fingerprint changes or the connection is updated/deleted.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.engine import Engine, URL

logger = logging.getLogger(__name__)

SUPPORTED_ENGINES = ("postgresql", "mysql", "mariadb", "sqlite")

_DRIVERS = {
    "postgresql": "postgresql+psycopg2",
    "mysql": "mysql+pymysql",
    "mariadb": "mysql+pymysql",
    "sqlite": "sqlite",
}

_DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306, "mariadb": 3306}

_cache_lock = threading.Lock()
_engine_cache: dict[int, tuple[str, Engine]] = {}


class UnsupportedEngineError(Exception):
    pass


def build_url(
    engine: str,
    host: Optional[str],
    port: Optional[int],
    database: Optional[str],
    username: Optional[str],
    password: Optional[str],
    options: Optional[dict[str, Any]] = None,
) -> URL:
    if engine not in _DRIVERS:
        raise UnsupportedEngineError(
            f"Unsupported engine '{engine}'. Supported: {', '.join(SUPPORTED_ENGINES)}"
        )
    if engine == "sqlite":
        # For SQLite `database` is the file path inside the container.
        return URL.create(drivername="sqlite", database=database or ":memory:")
    query = {str(k): str(v) for k, v in (options or {}).items()}
    return URL.create(
        drivername=_DRIVERS[engine],
        host=host,
        port=port or _DEFAULT_PORTS.get(engine),
        database=database,
        username=username,
        password=password or None,
        query=query,
    )


def _connect_args(engine: str) -> dict[str, Any]:
    if engine in ("postgresql", "mysql", "mariadb"):
        return {"connect_timeout": 5}
    return {}


def get_engine(connection_id: int, engine: str, url: URL) -> Engine:
    """Return a cached engine for this connection, rebuilding it if the URL changed."""
    fingerprint = url.render_as_string(hide_password=False)
    with _cache_lock:
        cached = _engine_cache.get(connection_id)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        if cached is not None:
            cached[1].dispose()
        eng = sa.create_engine(
            url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=3,
            pool_recycle=1800,
            connect_args=_connect_args(engine),
        )
        _engine_cache[connection_id] = (fingerprint, eng)
        return eng


def dispose_engine(connection_id: int) -> None:
    with _cache_lock:
        cached = _engine_cache.pop(connection_id, None)
    if cached is not None:
        try:
            cached[1].dispose()
        except Exception:  # noqa: BLE001
            pass
    close_tunnel(connection_id)


# --------------------------------------------------------------------------- #
# SSH tunnels
# --------------------------------------------------------------------------- #
# Un datasource dietro bastion apre un forward SSH locale verso host:porta del
# DB. Il tunnel vive quanto l'engine cache e viene chiuso da dispose_engine.
_tunnels: dict[int, Any] = {}
_tunnel_lock = threading.Lock()


def ensure_tunnel(
    connection_id: int, ssh_cfg: dict[str, Any], remote_host: str, remote_port: int
) -> tuple[str, int]:
    """Apre (o riusa) un tunnel SSH e ritorna l'endpoint locale (host, porta).

    ``ssh_cfg`` contiene host/port/username del bastion e, in chiaro, la
    password o la chiave privata a seconda di ``auth_method``.
    """
    from sshtunnel import SSHTunnelForwarder

    fingerprint = (
        ssh_cfg.get("host"), ssh_cfg.get("port"), ssh_cfg.get("username"),
        ssh_cfg.get("auth_method"), remote_host, remote_port,
    )
    with _tunnel_lock:
        existing = _tunnels.get(connection_id)
        if existing is not None:
            server, cached_fp = existing
            if cached_fp == fingerprint and server.is_active:
                return "127.0.0.1", server.local_bind_port
            _stop_tunnel(server)

        kwargs: dict[str, Any] = {
            "ssh_username": ssh_cfg.get("username"),
            "remote_bind_address": (remote_host, int(remote_port)),
            "local_bind_address": ("127.0.0.1", 0),
        }
        if ssh_cfg.get("auth_method") == "key":
            kwargs["ssh_pkey"] = _pkey_from_string(ssh_cfg.get("private_key") or "")
        else:
            kwargs["ssh_password"] = ssh_cfg.get("password")

        server = SSHTunnelForwarder(
            (ssh_cfg.get("host"), int(ssh_cfg.get("port") or 22)), **kwargs
        )
        server.start()
        _tunnels[connection_id] = (server, fingerprint)
        return "127.0.0.1", server.local_bind_port


def _pkey_from_string(pem: str):
    """Costruisce una chiave privata paramiko dal contenuto PEM."""
    import io

    import paramiko

    for loader in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            return loader.from_private_key(io.StringIO(pem))
        except Exception:  # noqa: BLE001 - prova il tipo di chiave successivo
            continue
    raise ValueError("Unsupported or invalid SSH private key")


def _stop_tunnel(server: Any) -> None:
    try:
        server.stop()
    except Exception:  # noqa: BLE001
        pass


def close_tunnel(connection_id: int) -> None:
    with _tunnel_lock:
        entry = _tunnels.pop(connection_id, None)
    if entry is not None:
        _stop_tunnel(entry[0])


def ping(engine: Engine) -> None:
    """Open a connection and run a trivial query; raises on failure."""
    with engine.connect() as conn:
        conn.execute(sa.text("SELECT 1"))


def probe_url(engine: str, url: URL) -> None:
    """One-shot connectivity check on an ephemeral engine (never cached); raises on failure."""
    eng = sa.create_engine(url, poolclass=sa.pool.NullPool, connect_args=_connect_args(engine))
    try:
        ping(eng)
    finally:
        eng.dispose()
