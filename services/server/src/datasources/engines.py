"""SQLAlchemy engine construction and caching for external database connections.

Engines are synchronous (executed via ``asyncio.to_thread`` by the service
layer) and cached per connection id and scope (database and schema the
engine is opened on); a cache entry is invalidated whenever its URL fingerprint
changes, and every entry of a connection when it is updated/deleted.
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
# Chiave: connessione più perimetro. Lo stesso server può essere aperto su
# database o schemi diversi da progetti diversi.
_engine_cache: dict[tuple[int, tuple], tuple[str, Engine]] = {}


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
    extra_query: Optional[dict[str, str]] = None,
) -> URL:
    if engine not in _DRIVERS:
        raise UnsupportedEngineError(
            f"Unsupported engine '{engine}'. Supported: {', '.join(SUPPORTED_ENGINES)}"
        )
    if engine == "sqlite":
        # For SQLite `database` is the file path inside the container.
        return URL.create(drivername="sqlite", database=database or ":memory:")
    query = {str(k): str(v) for k, v in (options or {}).items()}
    extra = {str(k): str(v) for k, v in (extra_query or {}).items()}
    # "options" porta i flag -c di sessione di PostgreSQL: un valore sovrascrive
    # l'altro solo qui, perché libpq applica i flag -c nell'ordine in cui
    # compaiono e l'ultimo per lo stesso parametro vince. Il perimetro si
    # accoda quindi a quelli del catalogo, così il search_path del perimetro
    # prevale senza perdere i flag di sessione già scritti sulla connessione.
    if "options" in query and "options" in extra:
        query["options"] = f"{query['options']} {extra['options']}"
        extra = {k: v for k, v in extra.items() if k != "options"}
    query.update(extra)
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


def _reset_statement(engine: str, database: Optional[str]) -> Optional[str]:
    """Il comando che riporta la sessione come nasce, o None se non serve.

    Su PostgreSQL ``DISCARD ALL`` rimette i parametri di sessione ai valori
    della connessione (quindi il search_path del perimetro, che viaggia nelle
    opzioni di connessione) e butta tabelle temporanee, cursori e piani.
    Su MySQL non c'è un comando equivalente: ``USE`` sul database della
    connessione ripristina almeno lo schema di default, che è la parte che
    decide il perimetro. SQLite non ha stato di sessione da ripulire.
    """
    if engine == "postgresql":
        return "DISCARD ALL"
    if engine in ("mysql", "mariadb") and database:
        quoted = str(database).replace("`", "``")
        return f"USE `{quoted}`"
    return None


def _reset_session_state(dbapi_connection: Any, connection_record: Any, statement: str) -> None:
    """Esegue il comando di reset su una connessione che rientra nel pool.

    Il comando gira fuori da qualunque transazione, perché ``DISCARD ALL`` non
    è ammesso dentro un blocco di transazione e il driver ne apre uno da sé al
    primo comando. Una connessione che non si riesce a ripulire viene
    invalidata: il pool la butta e ne apre un'altra al prossimo checkout,
    invece di riusarne una con lo stato di sessione di prima.
    """
    if dbapi_connection is None:
        # Connessione già invalidata da un errore: non c'è niente da ripulire e
        # il pool ne aprirà una nuova.
        return
    autocommit_off = getattr(dbapi_connection, "autocommit", None) is False
    try:
        dbapi_connection.rollback()
        if autocommit_off:
            dbapi_connection.autocommit = True
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(statement)
        finally:
            cursor.close()
    except Exception:  # noqa: BLE001
        logger.warning("session reset failed on check-in, connection dropped", exc_info=True)
        try:
            connection_record.invalidate()
        except Exception:  # noqa: BLE001
            pass
    finally:
        if autocommit_off:
            try:
                dbapi_connection.autocommit = False
            except Exception:  # noqa: BLE001
                pass


def _install_session_reset(eng: Engine, engine: str, database: Optional[str]) -> None:
    """Ripulisce ogni connessione al rientro nel pool.

    Le connessioni del pool vengono riusate: senza questo, un cambio di
    sessione fatto e committato da una scrittura (per esempio un search_path
    spostato) resterebbe sulla connessione e le letture successive leggerebbero
    fuori dal perimetro. Il reset arriva dopo il rollback del pool e costa un
    comando per rientro, anche quando la connessione passa da un tunnel SSH.
    """
    statement = _reset_statement(engine, database)
    if statement is None:
        return

    @sa.event.listens_for(eng, "checkin")
    def _reset_on_checkin(dbapi_connection: Any, connection_record: Any) -> None:
        _reset_session_state(dbapi_connection, connection_record, statement)


def get_engine(connection_id: int, engine: str, url: URL, scope_key: tuple = ()) -> Engine:
    """Return a cached engine for this connection and scope, rebuilding it if the URL changed."""
    fingerprint = url.render_as_string(hide_password=False)
    key = (connection_id, scope_key)
    with _cache_lock:
        cached = _engine_cache.get(key)
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
        _install_session_reset(eng, engine, url.database)
        _engine_cache[key] = (fingerprint, eng)
        return eng


def dispose_engine(connection_id: int) -> None:
    """Chiude gli engine di tutti i perimetri di questa connessione, e il tunnel."""
    with _cache_lock:
        keys = [k for k in _engine_cache if k[0] == connection_id]
        cached = [_engine_cache.pop(k) for k in keys]
    for _, eng in cached:
        try:
            eng.dispose()
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


def ephemeral_engine(engine: str, url: URL) -> Engine:
    """Engine usa e getta: mai in cache e senza pool. Chi lo apre lo chiude con dispose()."""
    return sa.create_engine(url, poolclass=sa.pool.NullPool, connect_args=_connect_args(engine))


def probe_url(engine: str, url: URL) -> None:
    """One-shot connectivity check on an ephemeral engine (never cached); raises on failure."""
    eng = ephemeral_engine(engine, url)
    try:
        ping(eng)
    finally:
        eng.dispose()
