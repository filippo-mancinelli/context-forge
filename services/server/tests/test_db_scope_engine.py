from urllib.parse import urlparse

import sqlalchemy as sa

from src.datasources import engines
from tests.pgutil import TEST_DATABASE_URL, requires_pg


def test_extra_query_wins_over_catalog_options():
    url = engines.build_url(
        engine="postgresql", host="h", port=5432, database="app",
        username="u", password="p",
        options={"options": "-csearch_path=tutto", "connect_timeout": 7},
        extra_query={"options": '-csearch_path="vendite"'},
    )
    # Il perimetro si accoda: per lo stesso parametro l'ultimo flag -c vince.
    assert url.query["options"] == '-csearch_path=tutto -csearch_path="vendite"'
    assert url.query["connect_timeout"] == "7"


def test_scope_keeps_catalog_session_flags():
    # I flag di sessione del catalogo restano; il perimetro si accoda per ultimo.
    url = engines.build_url(
        engine="postgresql", host="h", port=5432, database="app",
        username="u", password="p",
        options={"options": "-c default_transaction_read_only=on"},
        extra_query={"options": '-csearch_path="vendite"'},
    )
    assert url.query["options"] == '-c default_transaction_read_only=on -csearch_path="vendite"'

    # Senza opzioni sul catalogo, il perimetro resta l'unico valore.
    only_scope = engines.build_url(
        engine="postgresql", host="h", port=5432, database="app",
        username="u", password="p",
        options={},
        extra_query={"options": '-csearch_path="vendite"'},
    )
    assert only_scope.query["options"] == '-csearch_path="vendite"'


def test_cache_separates_scopes_of_the_same_connection(monkeypatch):
    created = []
    original_create_engine = sa.create_engine

    def fake_create_engine(url, **kwargs):
        created.append(url.render_as_string(hide_password=False))
        return original_create_engine("sqlite://")

    monkeypatch.setattr(sa, "create_engine", fake_create_engine)
    engines._engine_cache.clear()

    base = dict(engine="postgresql", host="h", port=5432, username="u", password="p")
    vendite = engines.build_url(database="app", **base,
                                extra_query={"options": '-csearch_path="vendite"'})
    acquisti = engines.build_url(database="app", **base,
                                 extra_query={"options": '-csearch_path="acquisti"'})

    first = engines.get_engine(1, "postgresql", vendite, ("app", "vendite"))
    second = engines.get_engine(1, "postgresql", acquisti, ("app", "acquisti"))
    again = engines.get_engine(1, "postgresql", vendite, ("app", "vendite"))

    assert first is not second
    assert first is again
    assert len(created) == 2


def test_dispose_closes_every_scope_of_the_connection(monkeypatch):
    disposed = []

    class _FakeEngine:
        def __init__(self, tag):
            self.tag = tag

        def dispose(self):
            disposed.append(self.tag)

    engines._engine_cache.clear()
    engines._engine_cache[(5, ("app", "vendite"))] = ("fp1", _FakeEngine("vendite"))
    engines._engine_cache[(5, ("app", "acquisti"))] = ("fp2", _FakeEngine("acquisti"))
    engines._engine_cache[(6, ("app", "public"))] = ("fp3", _FakeEngine("altra"))

    engines.dispose_engine(5)

    assert sorted(disposed) == ["acquisti", "vendite"]
    assert list(engines._engine_cache) == [(6, ("app", "public"))]


# Round 5: una connessione del pool non viene ripulita al rientro, quindi un
# cambio di sessione committato da una scrittura (search_path spostato con
# set_config) resta sulla connessione e sposta il perimetro delle letture
# successive. Il reset al rientro deve riportarla come nasce.
class _FakeCursor:
    def __init__(self, connection, fail):
        self._connection = connection
        self._fail = fail
        self.closed = False

    def execute(self, statement):
        if self._fail:
            raise RuntimeError("boom")
        if not self._connection.autocommit:
            raise RuntimeError("cannot run inside a transaction block")
        self._connection.executed.append(statement)

    def close(self):
        self.closed = True


class _FakeDbapiConnection:
    def __init__(self, fail=False):
        self.autocommit = False
        self.executed = []
        self.rollbacks = 0
        self.cursors = []
        self._fail = fail

    def rollback(self):
        self.rollbacks += 1

    def cursor(self):
        cursor = _FakeCursor(self, self._fail)
        self.cursors.append(cursor)
        return cursor


class _FakeRecord:
    def __init__(self):
        self.invalidated = False

    def invalidate(self, exception=None):
        self.invalidated = True


def test_only_the_engines_with_session_state_are_reset():
    assert engines._reset_statement("postgresql", "app") == "DISCARD ALL"
    assert engines._reset_statement("mysql", "app") == "USE `app`"
    assert engines._reset_statement("mariadb", "app") == "USE `app`"
    # SQLite non ha stato di sessione da ripulire, e senza database di default
    # su MySQL non c'è niente da ripristinare.
    assert engines._reset_statement("sqlite", "/tmp/x.db") is None
    assert engines._reset_statement("mysql", None) is None


def test_the_session_reset_runs_outside_a_transaction_and_restores_autocommit():
    conn = _FakeDbapiConnection()
    record = _FakeRecord()

    engines._reset_session_state(conn, record, "DISCARD ALL")

    assert conn.executed == ["DISCARD ALL"]
    assert conn.rollbacks == 1
    assert conn.autocommit is False
    assert conn.cursors[0].closed is True
    assert record.invalidated is False


def test_a_connection_that_cannot_be_cleaned_is_invalidated():
    conn = _FakeDbapiConnection(fail=True)
    record = _FakeRecord()

    engines._reset_session_state(conn, record, "DISCARD ALL")

    assert record.invalidated is True
    assert conn.autocommit is False


def test_a_connection_already_gone_is_left_alone():
    record = _FakeRecord()
    engines._reset_session_state(None, record, "DISCARD ALL")
    assert record.invalidated is False


@requires_pg
def test_a_session_change_does_not_survive_the_return_to_the_pool():
    target = urlparse(TEST_DATABASE_URL)
    url = engines.build_url(
        engine="postgresql", host=target.hostname, port=target.port,
        database=(target.path or "").lstrip("/") or "postgres",
        username=target.username, password=target.password,
        extra_query={"options": '-csearch_path="vendite",public'},
    )
    engines._engine_cache.pop((9101, ("postgres", "vendite")), None)
    eng = engines.get_engine(9101, "postgresql", url, ("postgres", "vendite"))
    try:
        with eng.connect() as conn:
            scope_path = conn.execute(sa.text("SHOW search_path")).scalar()
            assert "vendite" in scope_path
            conn.execute(sa.text("SELECT set_config('search_path','acquisti,vendite',false)"))
            conn.commit()
            assert conn.execute(sa.text("SHOW search_path")).scalar() == "acquisti,vendite"

        # Stessa connessione del pool, ripulita al rientro: torna il perimetro.
        with eng.connect() as conn:
            assert conn.execute(sa.text("SHOW search_path")).scalar() == scope_path
            conn.execute(sa.text("CREATE TEMP TABLE t_scope_probe(x int)"))
            conn.commit()

        # Nemmeno le tabelle temporanee sopravvivono al rientro.
        with eng.connect() as conn:
            assert conn.execute(
                sa.text("SELECT to_regclass('t_scope_probe') IS NULL")
            ).scalar() is True
    finally:
        engines.dispose_engine(9101)
