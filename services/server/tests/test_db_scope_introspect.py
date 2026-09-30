import time
from types import SimpleNamespace

import sqlalchemy as sa

from src.datasources import engines, introspect


class _FakeEngine:
    """Engine finto: il dialetto, il database della URL e la chiusura."""

    def __init__(self, dialect, database=None):
        self.dialect = SimpleNamespace(name=dialect)
        self.url = SimpleNamespace(database=database)
        self.disposed = False

    def dispose(self):
        self.disposed = True


def test_postgres_lists_every_database_with_its_schemas(monkeypatch):
    main = _FakeEngine("postgresql", "app")
    opened = {}

    def open_database(name):
        opened[name] = _FakeEngine("postgresql", name)
        return opened[name]

    schemas = {
        "app": ["vendite", "public", "information_schema", "pg_catalog", "pg_toast",
                "pg_temp_3", "pg_toast_temp_3"],
        "crm": ["public"],
    }

    def fake_schema_names(engine):
        if engine.url.database == "chiuso":
            raise RuntimeError("permission denied for database chiuso")
        return schemas[engine.url.database]

    monkeypatch.setattr(introspect, "_pg_databases", lambda engine: ["app", "chiuso", "crm"])
    monkeypatch.setattr(introspect, "_schema_names", fake_schema_names)

    result = introspect.list_scopes(main, open_database)

    assert result == [
        {"database": "app", "schema": "public"},
        {"database": "app", "schema": "vendite"},
        {"database": "crm", "schema": "public"},
    ]
    # Il database corrente riusa l'engine già aperto; gli altri si chiudono dopo
    # l'uso, anche quando l'utenza non può connettersi.
    assert "app" not in opened
    assert opened["crm"].disposed and opened["chiuso"].disposed
    assert main.disposed is False


def test_a_passed_deadline_returns_what_was_seen_without_opening_more(monkeypatch):
    main = _FakeEngine("postgresql", "app")
    opened = {}

    def open_database(name):
        opened[name] = _FakeEngine("postgresql", name)
        return opened[name]

    monkeypatch.setattr(introspect, "_pg_databases", lambda engine: ["app", "crm", "storico"])
    monkeypatch.setattr(introspect, "_schema_names", lambda engine: ["public"])

    result = introspect.list_scopes(main, open_database, deadline=time.monotonic() - 1)

    # Scaduto il tempo concesso si restituisce quello che si è già visto: il
    # database su cui l'engine è aperto. Gli altri non vengono nemmeno aperti.
    assert result == [{"database": "app", "schema": "public"}]
    assert opened == {}


def test_mysql_lists_databases_without_the_system_ones(monkeypatch):
    monkeypatch.setattr(
        introspect, "_mysql_databases",
        lambda engine: ["sys", "crm", "information_schema", "mysql", "performance_schema", "app"],
    )
    result = introspect.list_scopes(_FakeEngine("mysql"), lambda name: None)
    assert result == [{"database": "app", "schema": None}, {"database": "crm", "schema": None}]


def test_sqlite_has_nothing_to_choose():
    engine = sa.create_engine("sqlite://")
    try:
        assert introspect.list_scopes(engine, lambda name: None) == []
    finally:
        engine.dispose()


def test_ephemeral_engine_is_never_pooled():
    url = engines.build_url("sqlite", None, None, ":memory:", None, None)
    eng = engines.ephemeral_engine("sqlite", url)
    try:
        assert isinstance(eng.pool, sa.pool.NullPool)
    finally:
        eng.dispose()
