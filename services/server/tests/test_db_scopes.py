import pytest

from src.datasources import engines, scopes


def test_shapes_cover_all_supported_engines():
    """Scope shapes must be defined for all supported engines."""
    assert set(scopes._SHAPES) == set(engines.SUPPORTED_ENGINES)


def test_shape_is_engine_specific():
    assert scopes.scope_shape("postgresql") == ("database", "schema")
    assert scopes.scope_shape("mysql") == ("database",)
    assert scopes.scope_shape("mariadb") == ("database",)
    assert scopes.scope_shape("sqlite") == ()


def test_postgres_needs_both_parts():
    assert scopes.normalize_scope("postgresql", "app", "public") == ("app", "public")
    # Lo schema mancante cade sul default del motore invece di restare vuoto.
    assert scopes.normalize_scope("postgresql", "app", None) == ("app", "public")
    with pytest.raises(scopes.ScopeShapeError, match="database"):
        scopes.normalize_scope("postgresql", None, "public")


def test_mysql_has_no_separate_schema():
    assert scopes.normalize_scope("mysql", "app", None) == ("app", None)
    # Schema e database coincidono: ripetere lo stesso valore è ammesso.
    assert scopes.normalize_scope("mysql", "app", "app") == ("app", None)
    with pytest.raises(scopes.ScopeShapeError, match="schema"):
        scopes.normalize_scope("mysql", "app", "other")
    with pytest.raises(scopes.ScopeShapeError, match="database"):
        scopes.normalize_scope("mariadb", "", None)


@pytest.mark.parametrize("bad", [
    "a b", "a\tb", "a\\b", "a'b", 'a"b', "a,b",
    "a\x00b", "a\nb", "a\rb", "a\x1fb", "a\x7fb",
])
def test_unsafe_identifiers_are_refused(bad):
    with pytest.raises(scopes.ScopeShapeError):
        scopes.normalize_scope("postgresql", "app", bad)
    with pytest.raises(scopes.ScopeShapeError):
        scopes.normalize_scope("postgresql", bad, "public")
    with pytest.raises(scopes.ScopeShapeError):
        scopes.normalize_scope("mysql", bad, None)


def test_mixed_case_and_punctuated_identifiers_are_kept():
    assert scopes.normalize_scope("postgresql", "App-1", "Vendite_2026") == ("App-1", "Vendite_2026")


def test_sqlite_has_no_scope():
    assert scopes.normalize_scope("sqlite", None, None) == (None, None)
    with pytest.raises(scopes.ScopeShapeError):
        scopes.normalize_scope("sqlite", "app", None)


def test_unsupported_engine_is_refused():
    with pytest.raises(scopes.ScopeShapeError, match="sqlserver"):
        scopes.normalize_scope("sqlserver", "app", "dbo")


def test_default_scope_comes_from_the_connection():
    pg = {"engine": "postgresql", "database_name": "app"}
    assert scopes.default_scope(pg) == ("app", "public")
    assert scopes.default_scope({"engine": "mysql", "database_name": "app"}) == ("app", None)
    assert scopes.default_scope({"engine": "sqlite", "database_name": "/data/a.db"}) == (None, None)


def test_introspection_schema_and_url_database():
    assert scopes.introspection_schema("postgresql", "app", "vendite") == "vendite"
    # Su MySQL lo schema da introspezionare è il database del perimetro.
    assert scopes.introspection_schema("mysql", "app", None) == "app"
    assert scopes.introspection_schema("sqlite", None, None) is None
    assert scopes.url_database("postgresql", "app", "altro") == "altro"
    # Senza perimetro si usa il database predefinito della connessione.
    assert scopes.url_database("postgresql", "app", None) == "app"
    assert scopes.url_database("sqlite", "/data/a.db", None) == "/data/a.db"


def test_connect_options_restrict_the_search_path_on_postgres():
    assert scopes.connect_options("postgresql", "vendite") == {"options": '-csearch_path="vendite"'}
    # Lo schema va tra virgolette: il maiuscolo resta quello del nome reale.
    assert scopes.connect_options("postgresql", "Vendite") == {"options": '-csearch_path="Vendite"'}
    assert scopes.connect_options("mysql", "app") == {}
    assert scopes.connect_options("sqlite", None) == {}


def test_label_reads_as_the_user_sees_it():
    assert scopes.scope_label("postgresql", "app", "vendite") == "app.vendite"
    assert scopes.scope_label("mysql", "app", None) == "app"
    assert scopes.scope_label("sqlite", None, None) == "(file)"


def test_label_names_the_default_database_when_the_scope_has_none():
    # Una connessione senza database usa quello predefinito del server: l'etichetta lo dice.
    assert scopes.scope_label("mysql", None, None) == "(default database)"
    assert scopes.scope_label("postgresql", "", None) == "(default database)"
    assert scopes.scope_label("sqlite", None, None) == "(file)"
