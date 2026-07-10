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
