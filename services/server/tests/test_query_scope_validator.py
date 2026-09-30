import pytest

from src.datasources.validator import (
    QueryValidationError,
    ScopeReferenceError,
    validate_query,
    validate_write_query,
)

# Perimetro PostgreSQL con schema "vendite".
ACCEPTED_IN_VENDITE = [
    "SELECT * FROM ordini",
    "SELECT * FROM vendite.ordini",
    "SELECT * FROM VENDITE.ordini",
    'SELECT * FROM "vendite"."ordini"',
    "SELECT o.id, c.nome FROM vendite.ordini o JOIN clienti AS c ON c.id = o.cliente_id",
    "SELECT ordini.id FROM ordini",
    "SELECT t.* FROM ordini t",
    "SELECT vendite.ordini.id FROM vendite.ordini",
    "WITH recenti AS (SELECT * FROM ordini) SELECT recenti.id FROM recenti",
    "WITH r(id) AS (SELECT 1) SELECT r.id FROM r",
    "WITH acquisti AS (SELECT 1 AS x) SELECT acquisti.x FROM acquisti",
    "SELECT s.n FROM (SELECT count(*) AS n FROM ordini) s",
    "SELECT * FROM ordini WHERE email = 'mario.rossi@acquisti.it'",
    "SELECT * FROM ordini WHERE nota = 'acquisti.segreti'",
    "SELECT $$acquisti.segreti$$ AS testo",
    'SELECT "acquisti.segreti" FROM ordini',
    "SELECT EXTRACT(YEAR FROM o.data) FROM ordini o",
    "SELECT vendite.totale(o.id) FROM ordini o",
    "SELECT lower(nome), 1.5, '2.5'::numeric FROM clienti",
    "SELECT o.id FROM ordini o WHERE o.id IN (SELECT r.ordine_id FROM righe r)",
    "SELECT x::vendite.stato FROM ordini",
    "EXPLAIN SELECT * FROM vendite.ordini",
]

REJECTED_IN_VENDITE = [
    ("SELECT * FROM acquisti.ordini", "acquisti.ordini"),
    ('SELECT * FROM "Vendite".ordini', "Vendite.ordini"),
    ("SELECT * FROM ordini o JOIN acquisti.fornitori f ON f.id = o.fornitore_id", "acquisti.fornitori"),
    ("SELECT * FROM ordini, acquisti.fornitori", "acquisti.fornitori"),
    # Un alias che porta il nome di un altro schema non rende legittimo quello schema nel FROM.
    ("SELECT * FROM ordini AS acquisti, acquisti.fornitori", "acquisti.fornitori"),
    ("WITH acquisti AS (SELECT 1 AS x) SELECT * FROM acquisti.fornitori", "acquisti.fornitori"),
    ("SELECT * FROM ordini WHERE id IN (SELECT id FROM acquisti.fornitori)", "acquisti.fornitori"),
    ("SELECT acquisti.prezzo(1)", "acquisti.prezzo"),
    ("SELECT * FROM ordini CROSS JOIN LATERAL acquisti.righe(ordini.id)", "acquisti.righe"),
    ("SELECT acquisti.x FROM ordini", "acquisti.x"),
    ("SELECT acquisti.* FROM ordini", "acquisti.*"),
    # Il nome a tre parti con il database davanti è rifiutato anche se punta al perimetro.
    ("SELECT * FROM app.vendite.ordini", "app.vendite.ordini"),
    ("EXPLAIN SELECT * FROM acquisti.ordini", "acquisti.ordini"),
    ("SELECT * FROM ordini UNION TABLE acquisti.ordini", "acquisti.ordini"),
]

# Perimetro MySQL: lo schema consentito è il database "app".
ACCEPTED_IN_APP = [
    "SELECT * FROM clienti",
    "SELECT * FROM app.clienti",
    "SELECT * FROM `app`.`clienti`",
    "SELECT c.`nome` FROM `clienti` c",
    "SHOW TABLES",
    "SHOW TABLES FROM app",
    "SHOW COLUMNS FROM clienti FROM app",
    "DESCRIBE clienti",
    "SELECT DATE_FORMAT(creato, '%Y.%m') FROM clienti",
]

REJECTED_IN_APP = [
    ("SELECT * FROM crm.clienti", "crm.clienti"),
    ("SELECT * FROM `crm`.`clienti`", "crm.clienti"),
    ("SELECT * FROM `crm`.clienti", "crm.clienti"),
    ("SELECT * FROM clienti c JOIN crm.ordini o ON o.cliente = c.id", "crm.ordini"),
    ("SHOW DATABASES", "databases"),
    ("SHOW SCHEMAS", "schemas"),
    ("SHOW TABLES FROM crm", "crm"),
    ("SHOW TABLES IN crm", "crm"),
    ("SHOW COLUMNS FROM clienti FROM crm", "crm"),
    ("SHOW COLUMNS FROM crm.clienti", "crm.clienti"),
    ("DESCRIBE crm.clienti", "crm.clienti"),
]

CATALOG_QUERIES = [
    "SELECT * FROM information_schema.tables",
    "SELECT table_name FROM INFORMATION_SCHEMA.TABLES",
    "SELECT * FROM ordini o JOIN information_schema.columns c ON c.table_name = 'ordini'",
    'SELECT * FROM "pg_catalog"."pg_class"',
    "SELECT * FROM ordini, pg_catalog.pg_namespace",
    # pg_catalog è sempre nel search_path: le sue relazioni si leggono anche senza prefisso.
    "SELECT nspname FROM pg_namespace",
    "SELECT pg_catalog.current_database()",
    "SELECT * FROM ordini WHERE s IN (SELECT schema_name FROM information_schema.schemata)",
    "SHOW TABLES FROM information_schema",
    "SELECT * FROM `information_schema`.`TABLES`",
]

# SELECT ... INTO scrive: una tabella nuova su PostgreSQL, variabili di sessione su MySQL.
SELECT_INTO_REJECTED = [
    "SELECT * INTO new_table FROM t",
    "SELECT a INTO TEMP x FROM t",
    "SELECT a INTO UNLOGGED x FROM t",
    "WITH c AS (SELECT 1) SELECT * INTO x FROM c",
    "SELECT a INTO @var FROM t",
    "select a into x from t",
]

SELECT_INTO_LOOKALIKES = [
    "SELECT 'into' AS w FROM t",
    "SELECT a FROM t WHERE note = 'x INTO y'",
    "SELECT into_date FROM t",
    'SELECT "into" FROM t',
    "SELECT `into` FROM t",
    "SELECT $$ a INTO b $$ AS testo",
]

WRITE_ACCEPTED = [
    "INSERT INTO ordini (id, nota) VALUES (1, 'acquisti.x')",
    "INSERT INTO vendite.ordini (id) VALUES (1)",
    "UPDATE ordini SET stato = 'chiuso' WHERE ordini.id = 3",
    "UPDATE vendite.ordini o SET stato = 1 WHERE o.id = 3",
    "DELETE FROM ordini WHERE id IN (SELECT ordine_id FROM righe)",
]

WRITE_REJECTED = [
    ("INSERT INTO acquisti.ordini (id) VALUES (1)", "acquisti.ordini"),
    ("INSERT INTO ordini (id) SELECT id FROM acquisti.ordini", "acquisti.ordini"),
    ("UPDATE acquisti.ordini SET stato = 1 WHERE id = 3", "acquisti.ordini"),
    ("UPDATE ordini SET prezzo = (SELECT max(p) FROM acquisti.listino) WHERE id = 3", "acquisti.listino"),
    ("DELETE FROM acquisti.ordini WHERE id = 3", "acquisti.ordini"),
    ("DELETE FROM ordini USING acquisti.resi r WHERE r.id = ordini.id", "acquisti.resi"),
]

# Vulnerabilità 1 (revisione di sicurezza): una stringa a apici singoli con un
# backslash finale si legge in due modi diversi a seconda del dialetto. Sotto
# la lettura sbagliata la query resta "chiusa" dentro una falsa stringa e il
# riferimento vero sparisce dal tokenizzatore, ma PostgreSQL con
# standard_conforming_strings=on (il default) la esegue con l'altra lettura.
BACKSLASH_DESYNC_CATALOG = [
    "SELECT 'a\\' AS x FROM pg_catalog.pg_class WHERE 'x'='x'",
    "SELECT 'a\\' AS x FROM information_schema.tables WHERE 'x'='x'",
]

BACKSLASH_DESYNC_SCOPE = [
    ("SELECT 'a\\' AS x FROM acquisti.ordini WHERE 'x'='x'", "acquisti.ordini"),
]

BACKSLASH_DESYNC_INTO = [
    "SELECT 'a\\' AS z INTO newtab FROM t WHERE c='x'",
]

BACKSLASH_DESYNC_WRITE = [
    ("INSERT INTO ordini (id, nota) SELECT id, 'a\\' FROM acquisti.ordini WHERE 'x'='x'", "acquisti.ordini"),
    (
        "UPDATE ordini SET nota=1 WHERE nota='a\\' AND x=(SELECT max(p) FROM acquisti.listino) AND y='z'",
        "acquisti.listino",
    ),
    (
        "DELETE FROM ordini WHERE id IN (1) AND nota='a\\' AND x IN (SELECT id FROM acquisti.resi) AND y='z'",
        "acquisti.resi",
    ),
]

# Vulnerabilità 2 (revisione di sicurezza): un livello tra parentesi che non
# comincia con SELECT/WITH/VALUES/TABLE veniva trattato come "non query", così
# una relazione dentro una lista di JOIN tra parentesi sfuggiva sia alla
# regola sui pg_* non qualificati sia al controllo del perimetro.
PAREN_FRAME_CATALOG_QUERIES = [
    "SELECT * FROM ((SELECT 1 AS a) UNION ALL SELECT relname FROM pg_class) s",
    "SELECT * FROM (pg_class cc JOIN pg_namespace nn ON cc.relnamespace = nn.oid) zz",
]

PAREN_FRAME_SCOPE_VIOLATIONS = [
    ("SELECT * FROM ((SELECT 1 AS acquisti) UNION ALL SELECT id FROM acquisti.fornitori) s", "acquisti.fornitori"),
    ("SELECT * FROM (ordini JOIN acquisti.fornitori ON 1=1) x WHERE 1 = (SELECT 1 AS acquisti)", "acquisti.fornitori"),
    ("SELECT * FROM (ordini JOIN acquisti.fornitori AS acquisti ON 1=1)", "acquisti.fornitori"),
]


@pytest.mark.parametrize("sql", ACCEPTED_IN_VENDITE)
def test_references_inside_the_postgres_scope_pass(sql):
    assert validate_query(sql, max_rows=10, allowed_schema="vendite")


@pytest.mark.parametrize("sql, reference", REJECTED_IN_VENDITE)
def test_references_outside_the_postgres_scope_are_refused(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_query(sql, max_rows=10, allowed_schema="vendite")
    assert exc.value.reference == reference
    assert exc.value.allowed_schema == "vendite"
    assert reference in str(exc.value) and "vendite" in str(exc.value)


def test_quoted_schema_is_compared_exactly():
    assert validate_query('SELECT * FROM "Vendite".ordini', max_rows=10, allowed_schema="Vendite")
    with pytest.raises(ScopeReferenceError):
        validate_query('SELECT * FROM "vendite".ordini', max_rows=10, allowed_schema="Vendite")


@pytest.mark.parametrize("sql", ACCEPTED_IN_APP)
def test_references_inside_the_mysql_database_pass(sql):
    assert validate_query(sql, max_rows=10, allowed_schema="app")


@pytest.mark.parametrize("sql, reference", REJECTED_IN_APP)
def test_references_to_another_mysql_database_are_refused(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_query(sql, max_rows=10, allowed_schema="app")
    assert exc.value.reference == reference


@pytest.mark.parametrize("allowed", [None, "vendite", "app"])
@pytest.mark.parametrize("sql", CATALOG_QUERIES)
def test_system_catalogs_are_always_refused(sql, allowed):
    with pytest.raises(QueryValidationError, match="system catalogs") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM acquisti.ordini",
    "SELECT * FROM crm.clienti",
    "SELECT acquisti.prezzo(1)",
    # Il confronto del bersaglio di FROM/IN con lo schema consentito resta
    # legato al perimetro: senza perimetro non c'e' niente con cui confrontarlo.
    "SHOW TABLES FROM crm",
    "SHOW COLUMNS FROM clienti FROM crm",
])
def test_without_a_scope_the_target_of_a_scoped_show_is_not_compared(sql):
    assert validate_query(sql, max_rows=10)


def test_limit_is_still_injected_after_the_scope_check():
    assert validate_query("SELECT * FROM vendite.ordini", max_rows=7, allowed_schema="vendite") \
        == "SELECT * FROM vendite.ordini LIMIT 7"


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", SELECT_INTO_REJECTED)
def test_select_into_is_refused_on_any_scope(sql, allowed):
    with pytest.raises(QueryValidationError, match=r"SELECT \.\.\. INTO"):
        validate_query(sql, max_rows=10, allowed_schema=allowed)


@pytest.mark.parametrize("sql", SELECT_INTO_LOOKALIKES)
def test_into_inside_literals_and_names_is_not_select_into(sql):
    assert validate_query(sql, max_rows=10)


def test_into_outfile_keeps_its_blocked_keyword_message():
    with pytest.raises(QueryValidationError, match="INTO OUTFILE"):
        validate_query("SELECT * FROM t INTO OUTFILE '/tmp/x'")


@pytest.mark.parametrize("sql", WRITE_ACCEPTED)
def test_writes_inside_the_scope_pass(sql):
    assert validate_write_query(sql, allowed_schema="vendite") == sql


@pytest.mark.parametrize("sql, reference", WRITE_REJECTED)
def test_writes_outside_the_scope_are_refused(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_write_query(sql, allowed_schema="vendite")
    assert exc.value.reference == reference


def test_writes_to_the_catalogs_are_refused_without_a_scope():
    with pytest.raises(QueryValidationError, match="system catalogs"):
        validate_write_query("DELETE FROM pg_catalog.pg_class WHERE oid = 1")


def test_write_where_check_still_comes_first():
    with pytest.raises(QueryValidationError, match="WHERE"):
        validate_write_query("DELETE FROM acquisti.ordini", allowed_schema="vendite")


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", BACKSLASH_DESYNC_CATALOG)
def test_backslash_desync_still_shows_the_system_catalogs(sql, allowed):
    with pytest.raises(QueryValidationError, match="system catalogs") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("sql, reference", BACKSLASH_DESYNC_SCOPE)
def test_backslash_desync_still_shows_the_out_of_scope_reference(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_query(sql, max_rows=10, allowed_schema="vendite")
    assert exc.value.reference == reference


@pytest.mark.parametrize("sql", BACKSLASH_DESYNC_INTO)
def test_backslash_desync_still_shows_select_into(sql):
    with pytest.raises(QueryValidationError, match=r"SELECT \.\.\. INTO"):
        validate_query(sql, max_rows=10)


@pytest.mark.parametrize("sql, reference", BACKSLASH_DESYNC_WRITE)
def test_backslash_desync_still_shows_the_out_of_scope_reference_on_writes(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_write_query(sql, allowed_schema="vendite")
    assert exc.value.reference == reference


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", PAREN_FRAME_CATALOG_QUERIES)
def test_a_parenthesized_join_list_still_shows_the_system_catalogs(sql, allowed):
    with pytest.raises(QueryValidationError, match="system catalogs") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("sql, reference", PAREN_FRAME_SCOPE_VIOLATIONS)
def test_a_parenthesized_join_list_still_shows_the_out_of_scope_reference(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_query(sql, max_rows=10, allowed_schema="vendite")
    assert exc.value.reference == reference


def test_dollar_quote_tags_must_match_to_close_the_literal():
    # Prima del fix "$tag$ ... $altro$" (tag diversi) chiudeva comunque la
    # stringa dollar-quoted, nascondendo quello che sta in mezzo: qui un
    # riferimento ai cataloghi di sistema.
    with pytest.raises(QueryValidationError, match="system catalogs"):
        validate_query("SELECT $tag$ FROM pg_catalog.pg_class $other$", max_rows=10)


# Round 2 (revisione avversariale su PostgreSQL vero): il primo nome dentro
# una parentesi che apre una lista di relazioni (dopo FROM, JOIN, USING o una
# virgola nella lista del FROM) non ha un leader testuale davanti a sé, solo
# la parentesi: veniva classificato come colonna, sfuggendo sia alla regola
# sui pg_* non qualificati sia al controllo del perimetro, e il suo alias non
# entrava in "declared" (falso positivo sul secondo nome della stessa lista).
PAREN_HEAD_CATALOG_QUERIES = [
    "SELECT relname FROM (pg_class cc CROSS JOIN generate_series(1,1) g) z",
    "SELECT * FROM (pg_shadow s CROSS JOIN generate_series(1,1) g) z",
    "SELECT * FROM (pg_class cc LEFT JOIN ordini o ON true) zz",
    "SELECT * FROM (pg_class cc NATURAL JOIN ordini) z",
    "SELECT * FROM (pg_class AS cc CROSS JOIN ordini) z",
    "SELECT * FROM (pg_stat_activity a CROSS JOIN ordini o) z",
    "SELECT * FROM ((pg_class cc CROSS JOIN ordini o) y CROSS JOIN ordini o2) w",
    "SELECT * FROM (((pg_class cc CROSS JOIN ordini o) y CROSS JOIN ordini o2) z CROSS JOIN ordini o3) w",
    "WITH q AS (SELECT * FROM (pg_class cc CROSS JOIN ordini o) z) SELECT * FROM q",
    "SELECT * FROM ordini o2, (pg_class cc CROSS JOIN ordini o) z",
    "SELECT * FROM ordini o INTERSECT SELECT * FROM (pg_class cc CROSS JOIN ordini o) z",
    "SELECT * FROM (pg_class)",
    "SELECT * FROM (pg_class) zz",
    "SELECT * FROM (pg_class cc, ordini o) z",
]

PAREN_HEAD_SCOPE_VIOLATIONS = [
    ("SELECT * FROM (acquisti.ordini a CROSS JOIN generate_series(1,1) g) acquisti", "acquisti.ordini"),
    ("SELECT * FROM (acquisti.fornitori f CROSS JOIN ordini o) acquisti", "acquisti.fornitori"),
    ("SELECT * FROM (acquisti.ordini a CROSS JOIN ordini o) AS acquisti", "acquisti.ordini"),
    ("SELECT * FROM (acquisti.ordini AS acquisti CROSS JOIN ordini o) zz", "acquisti.ordini"),
    ("SELECT 1 AS acquisti FROM (acquisti.fornitori f CROSS JOIN ordini o) z", "acquisti.fornitori"),
    ("WITH acquisti AS (SELECT 1) SELECT * FROM (acquisti.ordini a CROSS JOIN ordini o) x", "acquisti.ordini"),
    ("SELECT * FROM (acquisti.ordini) AS acquisti", "acquisti.ordini"),
    # Variante scoperta durante l'analisi: l'alias del secondo elemento di una
    # lista senza FROM interno ("headless") non deve poter riusare il nome
    # dello schema per farlo entrare in "declared" prima del controllo.
    ("SELECT * FROM (ordini o, acquisti.fornitori AS acquisti) z", "acquisti.fornitori"),
]

PAREN_HEAD_WRITE_CATALOG = [
    "DELETE FROM ordini USING (pg_class cc CROSS JOIN righe r) WHERE 1=1",
    "UPDATE ordini SET x=(SELECT count(*) FROM (pg_class cc CROSS JOIN righe r) z) WHERE id=1",
    "INSERT INTO ordini (a) SELECT count(*) FROM (pg_class cc CROSS JOIN righe r) z",
]

PAREN_HEAD_WRITE_VIOLATIONS = [
    ("DELETE FROM ordini USING (acquisti.ordini a CROSS JOIN righe r) acquisti WHERE 1=1", "acquisti.ordini"),
    ("UPDATE ordini SET x=1 FROM (acquisti.ordini a CROSS JOIN righe r) acquisti WHERE id=1", "acquisti.ordini"),
]

# Il primo elemento di una lista tra parentesi deve restare accettato quando è
# davvero nel perimetro, e il suo alias deve entrare in "declared" come quello
# di qualunque altra relazione: prima del fix la seconda query veniva
# rifiutata con un messaggio su "o.id" perché l'alias "o" non era dichiarato.
PAREN_HEAD_ACCEPTED = [
    "SELECT * FROM (ordini o JOIN righe r ON r.ordine_id = o.id) x",
    "SELECT * FROM (vendite.ordini o JOIN vendite.righe r ON r.ordine_id = o.id) z",
    "SELECT z.* FROM (ordini o JOIN righe r ON r.ordine_id = o.id) z",
    "SELECT * FROM ordini o JOIN (righe r JOIN articoli a ON a.id = r.articolo_id) j ON j.ordine_id = o.id",
]

# Le funzioni a argomenti separati da FROM restano un'eccezione: la parentesi
# che aprono non è una lista di relazioni, anche dopo il fix sul primo nome.
FROM_ARG_FUNCTION_QUERIES = [
    "SELECT EXTRACT(YEAR FROM o.data) FROM ordini o",
    "SELECT SUBSTRING(nome FROM 1 FOR 3) FROM clienti",
    "SELECT TRIM(FROM nome) FROM clienti",
    "SELECT OVERLAY(nome PLACING 'x' FROM 1) FROM clienti",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", PAREN_HEAD_CATALOG_QUERIES)
def test_the_head_of_a_parenthesized_relation_list_still_shows_the_system_catalogs(sql, allowed):
    with pytest.raises(QueryValidationError, match="system catalogs") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("sql, reference", PAREN_HEAD_SCOPE_VIOLATIONS)
def test_the_head_of_a_parenthesized_relation_list_still_shows_the_out_of_scope_reference(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_query(sql, max_rows=10, allowed_schema="vendite")
    assert exc.value.reference == reference


@pytest.mark.parametrize("sql", PAREN_HEAD_WRITE_CATALOG)
def test_the_head_of_a_parenthesized_relation_list_still_shows_the_system_catalogs_on_writes(sql):
    with pytest.raises(QueryValidationError, match="system catalogs") as exc:
        validate_write_query(sql, allowed_schema="vendite")
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("sql, reference", PAREN_HEAD_WRITE_VIOLATIONS)
def test_the_head_of_a_parenthesized_relation_list_still_shows_the_out_of_scope_reference_on_writes(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_write_query(sql, allowed_schema="vendite")
    assert exc.value.reference == reference


@pytest.mark.parametrize("sql", PAREN_HEAD_ACCEPTED)
def test_the_head_of_a_parenthesized_relation_list_inside_the_scope_is_accepted(sql):
    assert validate_query(sql, max_rows=10, allowed_schema="vendite")


@pytest.mark.parametrize("sql", FROM_ARG_FUNCTION_QUERIES)
def test_from_argument_functions_stay_accepted(sql):
    assert validate_query(sql, max_rows=10, allowed_schema="vendite")


# Round 3 (nuova revisione avversariale su PostgreSQL vero): ON (la condizione
# di un JOIN) e WITH (di "WITH ORDINALITY") chiudevano la clausola FROM/USING
# in corso, così una virgola dopo di loro non veniva più riconosciuta come
# parte della lista di relazioni.
JOIN_LIST_CATALOG_QUERIES = [
    "SELECT cc.relname FROM ordini o JOIN righe r ON r.id = o.id, pg_class cc",
    "SELECT count(*) FROM ordini o JOIN righe r ON true, generate_series(1,1) g, pg_class cc",
    "SELECT count(*) FROM generate_series(1,2) WITH ORDINALITY t, pg_class cc",
]

JOIN_LIST_SCOPE_VIOLATIONS = [
    (
        "SELECT acquisti.segreto FROM ordini o JOIN righe r ON r.id = o.id, acquisti.fornitori AS acquisti",
        "acquisti.fornitori",
    ),
    (
        "SELECT acquisti.segreto FROM generate_series(1,2) WITH ORDINALITY t, acquisti.fornitori AS acquisti",
        "acquisti.fornitori",
    ),
]

JOIN_LIST_WRITE_CATALOG = [
    "DELETE FROM ordini USING righe r JOIN articoli a ON a.id = r.aid, pg_class cc WHERE 1=1",
    "INSERT INTO ordini (id) SELECT count(*) FROM righe r JOIN articoli a ON a.id=r.aid, pg_class cc",
]

JOIN_LIST_WRITE_VIOLATIONS = [
    (
        "UPDATE ordini SET x=1 FROM righe r JOIN articoli a ON a.id=r.aid, acquisti.fornitori AS acquisti "
        "WHERE ordini.id=1 AND acquisti.segreto LIKE 'DATO%'",
        "acquisti.fornitori",
    ),
]

# Il primo caso è il falso positivo aggirato dal fix (l'alias della seconda
# relazione della lista, dopo l'ON, deve entrare in "declared"); il secondo è
# il falso positivo introdotto dal fix del round 2: la USING di un JOIN porta
# colonne tra parentesi, non relazioni, e non va trattata come catalogo.
JOIN_LIST_ACCEPTED = [
    "SELECT * FROM ordini o JOIN righe r ON r.ordine_id = o.id, articoli a WHERE a.id = r.articolo_id",
    "SELECT * FROM ordini o JOIN righe r USING (pg_id)",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", JOIN_LIST_CATALOG_QUERIES)
def test_a_comma_after_a_join_condition_still_shows_the_system_catalogs(sql, allowed):
    with pytest.raises(QueryValidationError, match="system catalogs") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("sql, reference", JOIN_LIST_SCOPE_VIOLATIONS)
def test_a_comma_after_a_join_condition_still_shows_the_out_of_scope_reference(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_query(sql, max_rows=10, allowed_schema="vendite")
    assert exc.value.reference == reference


@pytest.mark.parametrize("sql", JOIN_LIST_WRITE_CATALOG)
def test_a_comma_after_a_join_condition_still_shows_the_system_catalogs_on_writes(sql):
    with pytest.raises(QueryValidationError, match="system catalogs") as exc:
        validate_write_query(sql, allowed_schema="vendite")
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("sql, reference", JOIN_LIST_WRITE_VIOLATIONS)
def test_a_comma_after_a_join_condition_still_shows_the_out_of_scope_reference_on_writes(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_write_query(sql, allowed_schema="vendite")
    assert exc.value.reference == reference


@pytest.mark.parametrize("sql", JOIN_LIST_ACCEPTED)
def test_join_conditions_and_join_using_column_lists_do_not_break_the_relation_list(sql):
    assert validate_query(sql, max_rows=10, allowed_schema="vendite")


# L'euristica sui riferimenti non vede i nomi passati come stringa: queste
# funzioni leggono dati o file per nome (una query, una tabella, un file) e
# sono rifiutate per nome, a prescindere dal perimetro.
BLOCKED_FUNCTION_NAMES = [
    "query_to_xml",
    "query_to_xmlschema",
    "query_to_xml_and_xmlschema",
    "table_to_xml",
    "database_to_xml",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_ls_logdir",
    "pg_ls_waldir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_exec",
    "dblink_connect",
    "pg_stat_get_activity",
    "load_file",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("name", BLOCKED_FUNCTION_NAMES)
def test_data_reading_functions_are_refused_by_name(name, allowed):
    with pytest.raises(QueryValidationError, match="outside the scope") as exc:
        validate_query(f"SELECT {name}('x')", max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


def test_the_reported_data_exfiltration_function_call_is_refused():
    with pytest.raises(QueryValidationError, match="outside the scope"):
        validate_query(
            "SELECT query_to_xml('select * from acquisti.fornitori',true,false,'')",
            max_rows=10,
        )


def test_data_reading_functions_are_refused_on_writes_too():
    with pytest.raises(QueryValidationError, match="outside the scope"):
        validate_write_query(
            "INSERT INTO ordini (id) SELECT dblink_exec('...')",
            allowed_schema="vendite",
        )


# Trovato durante l'analisi del round 3, non nell'elenco della revisione:
# LATERAL non era tra i leader di relazione, quindi una parentesi (o un nome
# nudo) dopo LATERAL sfuggiva sia al seme del round 2 sia al leader diretto.
LATERAL_CATALOG_QUERIES = [
    "SELECT * FROM ordini o, LATERAL (pg_class cc CROSS JOIN generate_series(1,1) g) z",
    "SELECT * FROM ordini o JOIN LATERAL (pg_class cc CROSS JOIN generate_series(1,1) g) z ON true",
]

LATERAL_SCOPE_VIOLATIONS = [
    ("SELECT * FROM ordini o, LATERAL (acquisti.fornitori f CROSS JOIN ordini o2) z", "acquisti.fornitori"),
]

LATERAL_ACCEPTED = [
    "SELECT * FROM ordini o, LATERAL (SELECT * FROM righe r WHERE r.ordine_id = o.id) x",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", LATERAL_CATALOG_QUERIES)
def test_lateral_still_shows_the_system_catalogs(sql, allowed):
    with pytest.raises(QueryValidationError, match="system catalogs") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("sql, reference", LATERAL_SCOPE_VIOLATIONS)
def test_lateral_still_shows_the_out_of_scope_reference(sql, reference):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_query(sql, max_rows=10, allowed_schema="vendite")
    assert exc.value.reference == reference


@pytest.mark.parametrize("sql", LATERAL_ACCEPTED)
def test_lateral_subqueries_inside_the_scope_are_accepted(sql):
    assert validate_query(sql, max_rows=10, allowed_schema="vendite")


# Round 4 (revisione avversariale su PostgreSQL vero): un identificatore con
# escape Unicode, U&"..." con l'eventuale UESCAPE che ne cambia il carattere di
# escape, non veniva riconosciuto come un solo token: il validator leggeva un
# nome diverso da quello che il server risolve, e cosi' sfuggivano insieme la
# regola sui pg_* non qualificati, il blocco di information_schema e l'elenco
# delle funzioni che leggono dati per nome. Le stringhe sono raw: il backslash
# fa parte del SQL da provare, non è un escape di Python.
UNICODE_IDENTIFIER_QUERIES = [
    r"""SELECT relname FROM u&"\0070g_class" WHERE relname LIKE 'fornitori%' LIMIT 3""",
    r"""SELECT usename, passwd FROM U&"\0070g_shadow" """,
    r"""SELECT cc.relname FROM ordini o, U&"\0070g_class" cc WHERE cc.relname='fornitori'""",
    r"""SELECT count(*) FROM U&"!0070g_class" UESCAPE '!'""",
    r"""SELECT table_schema, table_name FROM U&"\0069nformation_schema".tables""",
    r"""SELECT U&"\0071uery_to_xml"('select * from acquisti.fornitori',true,false,'')""",
    r"""SELECT U&"!0071uery_to_xml" UESCAPE '!'('select * from acquisti.fornitori',true,false,'')""",
    r"""SELECT U&"\0070g_read_file"('/etc/hostname')""",
    # Anche il prefisso di uno schema fuori perimetro si puo' scrivere cosi'.
    r"""SELECT * FROM U&"\0061cquisti".fornitori""",
]

UNICODE_IDENTIFIER_WRITE_QUERIES = [
    r"""DELETE FROM U&"\0070g_class" WHERE oid = 1""",
    r"""INSERT INTO ordini (id) SELECT 1 WHERE U&"\0071uery_to_xml"("""
    r"""'select * from acquisti.fornitori',true,false,'')::text LIKE '%DATO%'""",
    r"""UPDATE ordini SET x = 1 FROM U&"\0061cquisti".fornitori f WHERE ordini.id = 1""",
]

# Una stringa con escape Unicode, U&'...', resta accettata: e' un dato, non un
# nome, e il suo contenuto non partecipa alla risoluzione dei nomi (i nomi
# dentro le stringhe sono un limite dichiarato del controllo).
UNICODE_STRING_ACCEPTED = [
    r"SELECT * FROM ordini WHERE nota = U&'\0061cquisti.segreti'",
    r"SELECT u&'\0070g_class' AS testo FROM ordini",
    r"SELECT * FROM ordini WHERE nota = U&'!0061cquisti' UESCAPE '!'",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", UNICODE_IDENTIFIER_QUERIES)
def test_unicode_escape_identifiers_are_refused(sql, allowed):
    with pytest.raises(QueryValidationError, match=r'U&"') as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", UNICODE_IDENTIFIER_WRITE_QUERIES)
def test_unicode_escape_identifiers_are_refused_on_writes_too(sql, allowed):
    with pytest.raises(QueryValidationError, match=r'U&"'):
        validate_write_query(sql, allowed_schema=allowed)


@pytest.mark.parametrize("sql", UNICODE_STRING_ACCEPTED)
def test_unicode_escape_strings_stay_accepted(sql):
    assert validate_query(sql, max_rows=10, allowed_schema="vendite")


# La famiglia *_to_xml/_to_xmlschema legge tabelle, schemi o l'intero database
# per nome e va bloccata per intero, non a metà; lo_get legge i byte di un large
# object, che non sta in nessuno schema.
XML_FAMILY_FUNCTION_NAMES = [
    "table_to_xml",
    "table_to_xmlschema",
    "table_to_xml_and_xmlschema",
    "query_to_xml",
    "query_to_xmlschema",
    "query_to_xml_and_xmlschema",
    "cursor_to_xml",
    "cursor_to_xmlschema",
    "schema_to_xml",
    "schema_to_xmlschema",
    "schema_to_xml_and_xmlschema",
    "database_to_xml",
    "database_to_xmlschema",
    "database_to_xml_and_xmlschema",
    "lo_get",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("name", XML_FAMILY_FUNCTION_NAMES)
def test_the_whole_xml_dump_family_is_refused_by_name(name, allowed):
    with pytest.raises(QueryValidationError, match="outside the scope") as exc:
        validate_query(f"SELECT {name}('x',true,false,'')", max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


XML_FAMILY_REPORTED_QUERIES = [
    "SELECT schema_to_xml('acquisti',true,false,'')",
    "SELECT schema_to_xml_and_xmlschema('acquisti',true,false,'')",
    "SELECT table_to_xml_and_xmlschema('acquisti.fornitori',true,false,'')",
    "SELECT database_to_xml_and_xmlschema(true,false,'')",
    "SELECT table_to_xmlschema('acquisti.fornitori',true,false,'')",
    "SELECT schema_to_xmlschema('acquisti',true,false,'')",
    "SELECT database_to_xmlschema(true,false,'')",
    "SELECT lo_get(16384)",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", XML_FAMILY_REPORTED_QUERIES)
def test_the_reported_xml_dump_calls_are_refused(sql, allowed):
    with pytest.raises(QueryValidationError, match="outside the scope"):
        validate_query(sql, max_rows=10, allowed_schema=allowed)


def test_the_xml_dump_family_is_refused_as_a_write_path_oracle():
    with pytest.raises(QueryValidationError, match="outside the scope"):
        validate_write_query(
            "INSERT INTO ordini (id) SELECT 1 WHERE "
            "schema_to_xml('acquisti',true,false,'')::text LIKE '%DATO%'",
            allowed_schema="vendite",
        )


# Round 5 (terza revisione avversariale su PostgreSQL vero): tre modi di leggere
# fuori dal perimetro che non passano per un nome SQL.
# 1. set_config cambia il search_path della sessione; sulla connessione del pool
#    il cambiamento sopravvive alla scrittura che lo committa e sposta il
#    perimetro di tutte le letture successive.
SESSION_FUNCTION_QUERIES = [
    "SELECT set_config('search_path','acquisti,vendite',false)",
    "SELECT * FROM ordini WHERE set_config('search_path','acquisti',false) IS NOT NULL",
    "SELECT pg_catalog.set_config('search_path','acquisti',false)",
    "SELECT setseed(0.5)",
    "SELECT pg_advisory_lock(1)",
    "SELECT pg_try_advisory_lock(1)",
    "SELECT pg_advisory_unlock_all()",
    "SELECT pg_reload_conf()",
    "SELECT pg_cancel_backend(123)",
    "SELECT pg_terminate_backend(123)",
    "SELECT pg_stat_reset()",
]

SESSION_FUNCTION_WRITE_QUERIES = [
    "UPDATE vendite.ordini SET importo = importo WHERE id = 1 "
    "AND set_config('search_path','acquisti,vendite',false) IS NOT NULL",
    "INSERT INTO ordini (id, nota) VALUES (1, set_config('search_path','acquisti',false))",
    "DELETE FROM ordini WHERE id = 1 AND setseed(0.5) IS NULL",
]

# Le funzioni che leggono lo stato della sessione restano ammesse: cambiano
# niente e non spostano il perimetro.
SESSION_READ_ACCEPTED = [
    "SELECT current_setting('search_path')",
    "SELECT * FROM ordini WHERE nota = current_setting('search_path')",
    "SELECT current_schema()",
]

# 2. lo_get era bloccata ma la coppia lo_open/loread leggeva gli stessi byte:
#    un large object non sta in nessuno schema, quindi è sempre fuori perimetro.
LARGE_OBJECT_QUERIES = [
    "SELECT loread(lo_open(16384, 262144), 100000)",
    "SELECT lo_open(16384, 262144)",
    "SELECT lo_lseek(0, 0, 0)",
    "SELECT lo_lseek64(0, 0, 0)",
    "SELECT lo_tell(0)",
    "SELECT lo_tell64(0)",
    "SELECT lo_close(0)",
    "SELECT lo_unlink(16384)",
    "SELECT lo_truncate(0, 0)",
    "SELECT lowrite(0, 'x'::bytea)",
    "SELECT lo_put(16384, 0, 'x'::bytea)",
    "SELECT lo_from_bytea(0, 'x'::bytea)",
    "SELECT lo_get(16384)",
]

# 3. pg_stat_get_activity era bloccata, ma la famiglia pg_stat_get_backend_*
#    legge la query di un'altra sessione, letterali compresi: morde quando più
#    perimetri condividono la stessa utenza del database.
BACKEND_ACTIVITY_QUERIES = [
    "SELECT pg_stat_get_backend_activity(s.id) FROM pg_stat_get_backend_idset() s(id)",
    "SELECT pg_stat_get_backend_idset()",
    "SELECT pg_stat_get_backend_activity(1)",
    "SELECT pg_stat_get_backend_activity_start(1)",
    "SELECT pg_stat_get_backend_client_addr(1)",
    "SELECT pg_stat_get_backend_userid(1)",
    "SELECT pg_stat_get_backend_pid(1)",
    "SELECT pg_stat_get_activity(NULL)",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", SESSION_FUNCTION_QUERIES)
def test_session_changing_functions_are_refused(sql, allowed):
    with pytest.raises(QueryValidationError, match="changes the session") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", SESSION_FUNCTION_WRITE_QUERIES)
def test_session_changing_functions_are_refused_on_writes_too(sql, allowed):
    with pytest.raises(QueryValidationError, match="changes the session"):
        validate_write_query(sql, allowed_schema=allowed)


@pytest.mark.parametrize("sql", SESSION_READ_ACCEPTED)
def test_functions_that_only_read_the_session_stay_accepted(sql):
    assert validate_query(sql, max_rows=10, allowed_schema="vendite")


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", LARGE_OBJECT_QUERIES)
def test_the_large_object_family_is_refused(sql, allowed):
    with pytest.raises(QueryValidationError, match="outside the scope") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


def test_the_large_object_family_is_refused_on_writes_too():
    with pytest.raises(QueryValidationError, match="outside the scope"):
        validate_write_query(
            "INSERT INTO ordini (nota) SELECT loread(lo_open(16384, 262144), 100)",
            allowed_schema="vendite",
        )


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", BACKEND_ACTIVITY_QUERIES)
def test_the_backend_activity_family_is_refused(sql, allowed):
    with pytest.raises(QueryValidationError, match="outside the scope") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


def test_the_backend_activity_family_is_refused_on_writes_too():
    with pytest.raises(QueryValidationError, match="outside the scope"):
        validate_write_query(
            "INSERT INTO ordini (nota) SELECT pg_stat_get_backend_activity(s.id) "
            "FROM pg_stat_get_backend_idset() s(id)",
            allowed_schema="vendite",
        )


# Trovate durante l'analisi del round 5, non nell'elenco della revisione: nelle
# famiglie già bloccate restavano sorelle raggiungibili con lo stesso effetto.
SIBLING_FUNCTION_QUERIES = [
    "SELECT pg_ls_tmpdir()",
    "SELECT pg_ls_archive_statusdir()",
    "SELECT pg_ls_logicalsnapdir()",
    "SELECT pg_ls_replslotdir()",
    "SELECT lo_import_with_oid('/etc/hostname', 16384)",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", SIBLING_FUNCTION_QUERIES)
def test_the_siblings_of_the_blocked_file_functions_are_refused(sql, allowed):
    with pytest.raises(QueryValidationError, match="outside the scope"):
        validate_query(sql, max_rows=10, allowed_schema=allowed)


# Round 6 (revisione finale del ramo): il perimetro copriva solo due forme di
# SHOW, e nelle liste di funzioni restavano famiglie intere raggiungibili.
# 1. SHOW è una superficie larga: oltre all'elenco dei database ci sono i grant
#    dell'utenza (che elencano ogni database raggiungibile), le sessioni degli
#    altri collegamenti con il testo delle loro query e la configurazione del
#    server. Dentro un perimetro passano solo le forme che parlano degli
#    oggetti del database o dello schema su cui la connessione è aperta.
UNSCOPED_SHOW_QUERIES = [
    "SHOW GRANTS",
    "SHOW GRANTS FOR CURRENT_USER",
    "SHOW FULL PROCESSLIST",
    "SHOW PROCESSLIST",
    "SHOW ENGINE INNODB STATUS",
    "SHOW VARIABLES",
    "SHOW STATUS",
    "SHOW GLOBAL VARIABLES LIKE 'datadir'",
    "SHOW GLOBAL STATUS",
    "SHOW BINARY LOGS",
    "SHOW MASTER STATUS",
    "SHOW SLAVE STATUS",
    "SHOW ENGINES",
    "SHOW PLUGINS",
    "SHOW PRIVILEGES",
    "SHOW WARNINGS",
    "SHOW ERRORS",
    "SHOW PROFILES",
    "SHOW OPEN TABLES",
    "SHOW COLLATION",
    "SHOW CHARACTER SET",
    "SHOW EVENTS",
    "SHOW FUNCTION STATUS",
    "SHOW PROCEDURE STATUS",
    "SHOW BINLOG EVENTS",
    "SHOW RELAYLOG EVENTS",
    "SHOW REPLICA STATUS",
    "SHOW ENGINE INNODB MUTEX",
    "SHOW PROFILE CPU",
    "SHOW EXTENDED INDEX FROM clienti",
    # Il nome dell'oggetto tra virgolette non e' la parola che nomina la forma:
    # resta una forma non riconosciuta, quindi rifiutata.
    "SHOW `DATABASES`",
    "SHOW",
]

SCOPED_SHOW_ACCEPTED = [
    "SHOW TABLES",
    "SHOW TABLES FROM app",
    "SHOW TABLES IN app",
    "SHOW TABLES LIKE 'cli%'",
    "SHOW FULL TABLES",
    "SHOW FULL TABLES FROM app",
    "SHOW COLUMNS FROM clienti",
    "SHOW COLUMNS FROM clienti FROM app",
    "SHOW FIELDS FROM clienti",
    "SHOW INDEX FROM clienti",
    "SHOW INDEXES FROM clienti FROM app",
    "SHOW KEYS FROM clienti",
    "SHOW TABLE STATUS",
    "SHOW TABLE STATUS FROM app",
    "SHOW FULL COLUMNS FROM clienti",
    "SHOW FULL COLUMNS FROM clienti FROM app",
    "SHOW FULL FIELDS FROM clienti",
    "SHOW TRIGGERS",
    "SHOW TRIGGERS FROM app",
    "SHOW TRIGGERS IN app LIKE 'cli%'",
]

# Le forme ammesse restano legate al perimetro nel bersaglio di FROM/IN.
SCOPED_SHOW_TARGET_REFUSED = [
    ("SHOW FULL COLUMNS FROM clienti FROM crm", "crm"),
    ("SHOW TRIGGERS FROM crm", "crm"),
    ("SHOW TRIGGERS IN crm", "crm"),
    ("SHOW FULL TABLES FROM crm", "crm"),
    ("SHOW TABLE STATUS FROM crm", "crm"),
]


@pytest.mark.parametrize("allowed", [None, "app"])
@pytest.mark.parametrize("sql", UNSCOPED_SHOW_QUERIES)
def test_show_forms_outside_the_scope_are_refused(sql, allowed):
    """Valgono su ogni perimetro, come le regole sui cataloghi di sistema: un
    perimetro dedotto non conferma niente, ma queste forme leggono il server,
    non il perimetro."""
    with pytest.raises(QueryValidationError, match="outside the scope") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("sql", ["SHOW DATABASES", "SHOW SCHEMAS", "show databases"])
def test_the_database_list_is_refused_without_a_scope_too(sql):
    """Con un perimetro l'elenco dei database e' un riferimento fuori
    perimetro; senza perimetro resta comunque una lettura del server."""
    with pytest.raises(QueryValidationError, match="outside the scope") as exc:
        validate_query(sql, max_rows=10)
    assert not isinstance(exc.value, ScopeReferenceError)


@pytest.mark.parametrize("sql, target", SCOPED_SHOW_TARGET_REFUSED)
def test_the_target_of_an_accepted_show_form_stays_in_the_scope(sql, target):
    with pytest.raises(ScopeReferenceError) as exc:
        validate_query(sql, max_rows=10, allowed_schema="app")
    assert exc.value.reference == target


@pytest.mark.parametrize("allowed", [None, "app"])
@pytest.mark.parametrize("sql", SCOPED_SHOW_ACCEPTED)
def test_the_scoped_show_forms_stay_accepted(sql, allowed):
    """La lista di ammessi vale su ogni perimetro: quello che parla degli
    oggetti del database resta leggibile anche dove il perimetro e' dedotto."""
    assert validate_query(sql, max_rows=10, allowed_schema=allowed)


def test_show_create_table_keeps_its_blocked_keyword_message():
    # CREATE resta tra le parole bloccate e risponde prima del perimetro.
    with pytest.raises(QueryValidationError, match="blocked operation: CREATE"):
        validate_query("SHOW CREATE TABLE clienti", max_rows=10, allowed_schema="app")


# 2. pg_show_all_settings e pg_show_all_file_settings restituiscono la
#    configurazione del server, fuori da qualunque schema.
SETTINGS_DUMP_QUERIES = [
    "SELECT * FROM pg_show_all_settings()",
    "SELECT pg_show_all_file_settings()",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", SETTINGS_DUMP_QUERIES)
def test_the_server_settings_functions_are_refused(sql, allowed):
    with pytest.raises(QueryValidationError, match="outside the scope") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


# 3. I lock di MySQL sono per sessione e restano presi sulla connessione del
#    pool: tenerne uno cambia lo stato della sessione delle letture successive.
LOCK_FUNCTION_QUERIES = [
    "SELECT get_lock('perimetro', 0)",
    "SELECT release_lock('perimetro')",
    "SELECT release_all_locks()",
    "SELECT is_used_lock('perimetro')",
    "SELECT is_free_lock('perimetro')",
]


@pytest.mark.parametrize("allowed", [None, "app"])
@pytest.mark.parametrize("sql", LOCK_FUNCTION_QUERIES)
def test_the_session_lock_functions_are_refused(sql, allowed):
    with pytest.raises(QueryValidationError, match="changes the session") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


def test_the_session_lock_functions_are_refused_on_writes_too():
    with pytest.raises(QueryValidationError, match="changes the session"):
        validate_write_query(
            "UPDATE ordini SET stato = 1 WHERE id = 1 AND get_lock('x', 0) = 1",
            allowed_schema="vendite",
        )


# 4. Di dblink erano bloccati tre nomi su nove: il resto della famiglia apre e
#    legge un collegamento verso qualunque database raggiungibile dal server.
DBLINK_FAMILY_QUERIES = [
    "SELECT dblink_connect_u('c', 'dbname=acquisti')",
    "SELECT dblink_send_query('c', 'select * from acquisti.fornitori')",
    "SELECT * FROM dblink_get_result('c') AS t(x text)",
    "SELECT dblink_open('c', 'cur', 'select * from acquisti.fornitori')",
    "SELECT dblink_fetch('c', 'cur', 100)",
    "SELECT dblink_close('c', 'cur')",
    "SELECT dblink_disconnect('c')",
    "SELECT dblink_is_busy('c')",
    "SELECT dblink_error_message('c')",
    "SELECT dblink_cancel_query('c')",
    "SELECT dblink_get_connections()",
    "SELECT dblink_get_notify()",
    "SELECT dblink_get_pkey('acquisti.fornitori')",
    "SELECT dblink_build_sql_insert('acquisti.fornitori', '1', 1, '{1}', '{2}')",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", DBLINK_FAMILY_QUERIES)
def test_the_whole_dblink_family_is_refused(sql, allowed):
    with pytest.raises(QueryValidationError, match="outside the scope") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


def test_the_dblink_family_is_refused_on_writes_too():
    with pytest.raises(QueryValidationError, match="outside the scope"):
        validate_write_query(
            "INSERT INTO ordini (nota) SELECT dblink_send_query('c', 'select 1')",
            allowed_schema="vendite",
        )


# 5. Su PostgreSQL una funzione senza argomenti si chiama anche senza
#    parentesi in posizione di relazione: l'elenco dei nomi bloccati vale
#    quindi anche per un nome in posizione di relazione.
PARENTHESIS_LESS_BLOCKED_RELATIONS = [
    "SELECT * FROM dblink_get_connections",
    "SELECT * FROM dblink_get_notify",
    "SELECT * FROM ordini JOIN dblink_get_connections ON true",
    "SELECT * FROM pg_ls_tmpdir",
    "SELECT * FROM pg_stat_get_backend_idset",
    "SELECT * FROM pg_show_all_file_settings",
    "SELECT * FROM lo_get",
    "SELECT * FROM load_file",
    # Le altre famiglie bloccate nella stessa posizione.
    "SELECT * FROM table_to_xml",
    "SELECT * FROM database_to_xmlschema",
    "SELECT * FROM schema_to_xml_and_xmlschema",
    "SELECT * FROM pg_read_binary_file",
    "SELECT * FROM lo_export",
    # Lo stesso nome raggiunto qualificato, tra virgolette doppie o backtick,
    # dopo ONLY o TABLE, dentro una parentesi in posizione di relazione, in una
    # sottoquery o come oggetto di DESCRIBE.
    "SELECT * FROM public.dblink_get_connections",
    'SELECT * FROM "dblink_get_connections"',
    "SELECT * FROM `dblink_get_connections`",
    "SELECT * FROM ONLY pg_ls_tmpdir",
    "SELECT * FROM ordini UNION TABLE dblink_get_connections",
    "SELECT * FROM (dblink_get_connections)",
    "SELECT * FROM ordini CROSS JOIN LATERAL pg_ls_tmpdir",
    "SELECT * FROM ordini WHERE id IN (SELECT x FROM dblink_get_notify)",
    "DESCRIBE dblink_get_connections",
]


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", PARENTHESIS_LESS_BLOCKED_RELATIONS)
def test_blocked_names_are_refused_in_a_relation_position_without_parentheses(sql, allowed):
    with pytest.raises(QueryValidationError, match="outside the scope") as exc:
        validate_query(sql, max_rows=10, allowed_schema=allowed)
    assert not isinstance(exc.value, ScopeReferenceError)


def test_a_blocked_name_without_parentheses_is_refused_on_writes_too():
    with pytest.raises(QueryValidationError, match="outside the scope"):
        validate_write_query(
            "INSERT INTO ordini (nota) SELECT x::text FROM dblink_get_connections x",
            allowed_schema="vendite",
        )


@pytest.mark.parametrize("sql", [
    "SELECT * FROM pg_advisory_unlock_all",
    "SELECT * FROM set_config",
    "SELECT * FROM pg_terminate_backend",
])
def test_session_changing_names_are_refused_in_a_relation_position_too(sql):
    with pytest.raises(QueryValidationError, match="changes the session"):
        validate_query(sql, max_rows=10, allowed_schema="vendite")


@pytest.mark.parametrize("allowed", [None, "vendite"])
@pytest.mark.parametrize("sql", [
    # Nomi di colonna: un nome bloccato conta in posizione di relazione o di
    # chiamata, non dove sta per una colonna, che da se' non legge niente.
    "SELECT lo_get FROM ordini",
    "SELECT * FROM ordini ORDER BY lo_get",
    # Nomi che cominciano come una famiglia bloccata senza esserne parte.
    "SELECT * FROM lo_getter",
    "SELECT * FROM dbl_link",
    "SELECT * FROM showcase",
    "SELECT * FROM table_to_xmlish",
    "SELECT * FROM loads",
])
def test_names_that_are_not_of_a_blocked_family_stay_accepted_as_relations(sql, allowed):
    assert validate_query(sql, max_rows=10, allowed_schema=allowed)


@pytest.mark.parametrize("allowed", [None, "vendite"])
def test_a_user_table_starting_with_pg_is_still_refused_as_a_catalog(allowed):
    # Il prefisso pg_ resta la regola sui cataloghi: il nome non e' di una
    # famiglia bloccata, quindi risponde con il messaggio dei cataloghi.
    with pytest.raises(QueryValidationError, match="system catalogs"):
        validate_query("SELECT * FROM pg_mia_tabella", max_rows=10, allowed_schema=allowed)


@pytest.mark.parametrize("sql", [
    # Nomi che cominciano per le stesse lettere senza essere della famiglia.
    "SELECT dblinks_totali FROM ordini",
    "SELECT showcase FROM ordini",
    "SELECT get_locked_by FROM ordini",
])
def test_names_that_only_start_like_a_blocked_family_stay_accepted(sql):
    assert validate_query(sql, max_rows=10, allowed_schema="vendite")
