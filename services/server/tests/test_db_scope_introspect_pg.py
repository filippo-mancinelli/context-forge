import sqlalchemy as sa

from src.datasources import engines, introspect
from tests.pgutil import database_url, requires_pg

pytestmark = requires_pg


def test_list_scopes_reads_databases_and_schemas_of_the_test_server(pg_database):
    url = sa.engine.make_url(database_url(pg_database)).set(drivername="postgresql+psycopg2")
    engine = engines.ephemeral_engine("postgresql", url)
    try:
        found = introspect.list_scopes(
            engine, lambda name: engines.ephemeral_engine("postgresql", url.set(database=name))
        )
    finally:
        engine.dispose()

    assert {"database": pg_database, "schema": "public"} in found
    # Né i cataloghi interni né i template sono perimetri da proporre.
    assert all(s["schema"] not in ("information_schema", "pg_catalog", "pg_toast") for s in found)
    assert all(not s["database"].startswith("template") for s in found)
