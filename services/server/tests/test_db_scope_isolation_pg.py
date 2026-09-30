import pytest

from src import config
from src.datasources import engines, introspect, service
from src.datasources.validator import QueryValidationError
from tests.pgutil import execute, fetch, fetchval, requires_pg, run_db, seed_org, seed_project

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


def _data(**over):
    data = {"name": "erp", "engine": "postgresql", "host": "127.0.0.1", "port": 5432,
            "database_name": "app", "username": "reader", "password": "secret",
            "options": {}, "description": None, "ssh_machine_id": None, "restricted": False}
    data.update(over)
    return data


async def seed_scope(project, connection_id, database, schema, alias, inferred) -> int:
    return await fetchval(
        "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
        "schema_name, alias, scope_inferred) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        project, connection_id, database, schema, alias, inferred,
    )


async def seed_two_projects() -> dict:
    """alpha collega erp su due schemi confermati e crm su un perimetro dedotto;
    beta collega crm. Gli id dei perimetri partono da 1001, lontani da quelli
    delle connessioni, così un id non si confonde con l'altro."""
    org = await seed_org("acme")
    alpha = await seed_project(org, "alpha")
    beta = await seed_project(org, "beta")
    erp = await service.create_connection(org, _data(name="erp"))
    crm = await service.create_connection(org, _data(name="crm", database_name="crm"))
    await execute("SELECT setval(pg_get_serial_sequence('project_db_scopes', 'id'), 1000)")
    return {
        "org": org, "alpha": alpha, "beta": beta, "erp": erp["id"], "crm": crm["id"],
        "vendite": await seed_scope(alpha, erp["id"], "app", "vendite", "erp-vendite", False),
        "acquisti": await seed_scope(alpha, erp["id"], "app", "acquisti", "erp-acquisti", False),
        "crm_alpha": await seed_scope(alpha, crm["id"], "crm", "public", "crm", True),
        "crm_beta": await seed_scope(beta, crm["id"], "crm", "public", "crm", True),
    }


def test_scope_id_opens_that_scope(pg_database):
    async def scenario():
        ids = await seed_two_projects()
        return (
            ids,
            await service.get_scope(ids["org"], ids["alpha"], ids["acquisti"]),
            await service.get_connection(ids["org"], ids["alpha"], ids["vendite"]),
        )

    ids, acquisti, vendite = run_db(scenario)
    assert acquisti["scope_id"] == ids["acquisti"] and acquisti["alias"] == "erp-acquisti"
    assert acquisti["scope_schema"] == "acquisti"
    # get_connection resta un sinonimo con la stessa lettura degli id.
    assert vendite["scope_id"] == ids["vendite"]


def test_connection_id_opens_its_only_scope(pg_database):
    async def scenario():
        ids = await seed_two_projects()
        return ids, await service.get_scope(ids["org"], ids["alpha"], ids["crm"])

    ids, record = run_db(scenario)
    assert record["scope_id"] == ids["crm_alpha"]


def test_connection_id_with_two_scopes_is_ambiguous(pg_database):
    async def scenario():
        ids = await seed_two_projects()
        with pytest.raises(service.ConnectionAmbiguousError) as exc:
            await service.get_scope(ids["org"], ids["alpha"], ids["erp"])
        return str(exc.value)

    message = run_db(scenario)
    assert "erp-vendite" in message and "erp-acquisti" in message


def test_alias_and_connection_name_still_resolve(pg_database):
    async def scenario():
        ids = await seed_two_projects()
        found = []
        for ref in ("erp-vendite", "ERP-ACQUISTI", "crm", str(ids["vendite"])):
            found.append((await service.get_scope(ids["org"], ids["alpha"], ref))["scope_id"])
        return ids, found

    ids, found = run_db(scenario)
    assert found == [ids["vendite"], ids["acquisti"], ids["crm_alpha"], ids["vendite"]]


def test_scope_of_another_project_is_not_reachable(pg_database):
    async def scenario():
        ids = await seed_two_projects()
        with pytest.raises(service.ConnectionNotFoundError):
            await service.get_scope(ids["org"], ids["alpha"], ids["crm_beta"])

    run_db(scenario)


async def seed_overlapping_ids() -> dict:
    """erp collega due perimetri, crm uno solo, senza spostare le sequenze:
    il secondo perimetro di erp e la connessione crm arrivano allo stesso id
    per costruzione (entrambi 2). È il caso che il fallback per id di
    connessione deve rifiutare invece di indovinare, perché lo stesso numero
    apre due letture diverse in questo progetto. bi collega un solo perimetro
    più avanti nelle due sequenze, con connessioni intermedie mai selezionate
    dal progetto: il suo id e quello del suo perimetro non toccano nessuno
    dei due spazi, a riprova che l'ambiguità scatta solo sulla vera
    sovrapposizione."""
    org = await seed_org("acme")
    alpha = await seed_project(org, "alpha")
    erp = await service.create_connection(org, _data(name="erp"))  # id 1
    crm = await service.create_connection(org, _data(name="crm", database_name="crm"))  # id 2
    erp_primo = await seed_scope(alpha, erp["id"], "app", "primo", "erp-primo", False)  # id 1
    erp_secondo = await seed_scope(alpha, erp["id"], "app", "secondo", "erp-secondo", False)  # id 2
    crm_scope = await seed_scope(alpha, crm["id"], "crm", "public", "crm", True)  # id 3
    await service.create_connection(org, _data(name="d1"))  # id 3, mai selezionato dal progetto
    await service.create_connection(org, _data(name="d2"))  # id 4, mai selezionato dal progetto
    bi = await service.create_connection(org, _data(name="bi", database_name="bi"))  # id 5
    bi_scope = await seed_scope(alpha, bi["id"], "bi", "public", "bi", True)  # id 4
    assert erp_secondo == crm["id"], "il test presuppone che l'id del secondo perimetro coincida con quello di crm"
    assert bi["id"] not in (erp_primo, erp_secondo, crm_scope, bi_scope)
    assert bi_scope not in (erp["id"], crm["id"], bi["id"])
    return {
        "org": org, "alpha": alpha, "erp": erp["id"], "crm": crm["id"], "bi": bi["id"],
        "erp_primo": erp_primo, "erp_secondo": erp_secondo, "crm_scope": crm_scope,
        "bi_scope": bi_scope,
    }


def test_overlapping_scope_and_connection_ids_are_ambiguous(pg_database):
    async def scenario():
        ids = await seed_overlapping_ids()
        with pytest.raises(service.ConnectionAmbiguousError) as exc:
            await service.get_scope(ids["org"], ids["alpha"], ids["erp_secondo"])
        return str(exc.value)

    message = run_db(scenario)
    assert "erp-secondo" in message and "crm" in message


def test_overlapping_ids_block_query_and_write(pg_database, monkeypatch):
    """Prima di eseguire qualunque cosa sul database di destinazione, la richiesta
    ambigua deve essere respinta: la risoluzione dell'engine non va mai raggiunta."""

    async def fail_if_reached(record):
        raise AssertionError("_resolve_engine non doveva essere chiamata su un id ambiguo")

    monkeypatch.setattr(service, "_resolve_engine", fail_if_reached)

    async def scenario():
        ids = await seed_overlapping_ids()
        with pytest.raises(service.ConnectionAmbiguousError):
            await service.run_query(ids["org"], ids["alpha"], ids["erp_secondo"], "SELECT 1")
        with pytest.raises(service.ConnectionAmbiguousError):
            await service.run_write(ids["org"], ids["alpha"], ids["erp_secondo"], "UPDATE t SET a = 1")

    run_db(scenario)


def test_only_scope_id_or_only_connection_id_still_resolve(pg_database):
    """Nello stesso progetto con id sovrapposti altrove, un id che tocca solo
    uno dei due spazi resta senza ambiguità: bi_scope non è l'id di nessuna
    connessione selezionata, l'id di bi non è quello di nessun perimetro."""

    async def scenario():
        ids = await seed_overlapping_ids()
        return (
            ids,
            await service.get_scope(ids["org"], ids["alpha"], ids["bi_scope"]),
            await service.get_scope(ids["org"], ids["alpha"], ids["bi"]),
        )

    ids, by_scope_id, by_connection_id = run_db(scenario)
    assert by_scope_id["scope_id"] == ids["bi_scope"] and by_scope_id["alias"] == "bi"
    assert by_connection_id["scope_id"] == ids["bi_scope"] and by_connection_id["alias"] == "bi"


async def seed_coincidence_ids() -> dict:
    """hr è la prima connessione creata in un database vuoto e collega il suo
    unico perimetro: le due sequenze partono entrambe da 1, quindi l'id della
    connessione e quello del perimetro coincidono per costruzione. È il caso
    comune di un sistema nuovo, che la lettura per id deve aprire senza
    ambiguità invece di rifiutare."""
    org = await seed_org("acme")
    alpha = await seed_project(org, "alpha")
    hr = await service.create_connection(org, _data(name="hr", database_name="hr"))  # id 1
    hr_scope = await seed_scope(alpha, hr["id"], "hr", "public", "hr", True)  # id 1
    assert hr_scope == hr["id"], "il test presuppone che l'id del perimetro coincida con quello della connessione"
    return {"org": org, "alpha": alpha, "hr": hr["id"], "hr_scope": hr_scope}


def test_scope_id_matching_its_own_connection_id_resolves(pg_database, monkeypatch):
    """Quando le due letture indicano lo stesso perimetro, la coincidenza è
    innocua: si apre normalmente e l'esecuzione raggiunge il motore, invece
    di essere respinta come nel caso di sovrapposizione fra letture diverse."""
    reached = {}

    async def fake_resolve_engine(record):
        reached["engine"] = True
        return "engine"

    def fake_execute_readonly(engine, sql, max_rows):
        return (["ok"], [{"ok": 1}], False)

    monkeypatch.setattr(service, "_resolve_engine", fake_resolve_engine)
    monkeypatch.setattr(service, "_execute_readonly", fake_execute_readonly)

    async def scenario():
        ids = await seed_coincidence_ids()
        scope = await service.get_scope(ids["org"], ids["alpha"], ids["hr_scope"])
        result = await service.run_query(
            ids["org"], ids["alpha"], ids["hr_scope"], "SELECT 1", source="sample"
        )
        return ids, scope, result

    ids, scope, result = run_db(scenario)
    assert scope["scope_id"] == ids["hr_scope"] == ids["hr"]
    assert reached.get("engine") is True
    assert result["rows"] == [{"ok": 1}]


from src.datasources.scopes import ScopeViolationError


def _no_remote(monkeypatch):
    seen = []
    monkeypatch.setattr(engines, "get_engine", lambda *args, **kwargs: object())

    def fake_overview(engine, schema=None):
        seen.append(("overview", schema))
        return {"schema": schema, "schemas": ["acquisti", "public", "vendite"], "tables": []}

    def fake_describe(engine, table, schema=None):
        seen.append(("describe", schema))
        return {"schema": schema, "columns": []}

    monkeypatch.setattr(introspect, "get_overview", fake_overview)
    monkeypatch.setattr(introspect, "describe_table", fake_describe)
    return seen


def test_confirmed_scope_refuses_another_schema(pg_database, monkeypatch):
    seen = _no_remote(monkeypatch)

    async def scenario():
        ids = await seed_two_projects()
        errors = []
        with pytest.raises(ScopeViolationError) as exc:
            await service.schema_overview(ids["org"], ids["alpha"], ids["vendite"], schema="acquisti")
        errors.append(exc.value)
        with pytest.raises(ScopeViolationError) as exc:
            await service.describe_table(ids["org"], ids["alpha"], ids["vendite"], "ordini",
                                         schema="acquisti")
        errors.append(exc.value)
        same = await service.schema_overview(ids["org"], ids["alpha"], ids["vendite"],
                                             schema="vendite")
        return errors, same

    errors, same = run_db(scenario)
    for error in errors:
        assert "acquisti" in str(error) and "app.vendite" in str(error)
        assert error.alias == "erp-vendite" and error.scope == "app.vendite"
        assert error.aliases == ["crm", "erp-acquisti", "erp-vendite"]
        assert "resource_select" in str(error)
    # Lo schema del perimetro è ammesso e gli altri schemi del server non compaiono.
    assert same["schema"] == "vendite" and same["schemas"] == ["vendite"]
    assert same["alias"] == "erp-vendite" and same["scope_label"] == "app.vendite"
    assert same["scope_inferred"] is False
    # Le richieste rifiutate non arrivano al server remoto.
    assert seen == [("overview", "vendite")]


def test_inferred_scope_keeps_honoring_an_explicit_schema(pg_database, monkeypatch):
    seen = _no_remote(monkeypatch)

    async def scenario():
        ids = await seed_two_projects()
        return await service.schema_overview(ids["org"], ids["alpha"], ids["crm_alpha"],
                                             schema="altro")

    overview = run_db(scenario)
    assert seen == [("overview", "altro")]
    assert overview["schemas"] == ["acquisti", "public", "vendite"]


def test_confirmed_scope_reads_and_writes_annotations_of_its_schema(pg_database):
    async def scenario():
        ids = await seed_two_projects()
        await service.upsert_annotations(ids["erp"], [
            {"schema_name": "", "table_name": "clienti", "description": "Anagrafica"},
            {"schema_name": "acquisti", "table_name": "fornitori", "description": "Fornitori"},
        ])
        seen = await service.scope_annotations(ids["org"], ids["alpha"], ids["vendite"])
        written = await service.save_scope_annotations(ids["org"], ids["alpha"], ids["vendite"], [
            {"schema_name": "", "table_name": "ordini", "column_name": "", "description": "Ordini"},
        ])
        with pytest.raises(ScopeViolationError):
            await service.save_scope_annotations(ids["org"], ids["alpha"], ids["vendite"], [
                {"schema_name": "acquisti", "table_name": "fornitori", "column_name": "",
                 "description": "Sovrascritto"},
            ])
        rows = await fetch(
            "SELECT schema_name, table_name, description FROM db_annotations ORDER BY table_name"
        )
        return seen, written, rows

    seen, written, rows = run_db(scenario)
    # Le descrizioni di un altro schema non si vedono; il jolly '' resta leggibile.
    assert [(a["schema_name"], a["table_name"]) for a in seen] == [("", "clienti")]
    assert written == 1
    # Lo schema mancante è quello del perimetro, e il rifiuto non ha scritto nulla.
    assert rows == [
        {"schema_name": "", "table_name": "clienti", "description": "Anagrafica"},
        {"schema_name": "acquisti", "table_name": "fornitori", "description": "Fornitori"},
        {"schema_name": "vendite", "table_name": "ordini", "description": "Ordini"},
    ]


def test_saving_under_a_confirmed_scope_clears_the_wildcard_twin(pg_database):
    """Un'annotazione scritta senza schema vale come jolly per ogni perimetro
    della connessione: salvandola da un perimetro confermato la riga nasce sul
    suo schema e il gemello senza schema va via, altrimenti resterebbe visibile
    agli altri perimetri e una descrizione cancellata ricomparirebbe."""
    async def scenario():
        ids = await seed_two_projects()
        await service.upsert_annotations(ids["erp"], [
            {"schema_name": "", "table_name": "ordini", "description": "Vecchia"},
            {"schema_name": "", "table_name": "ordini", "column_name": "stato",
             "description": "Vecchia colonna"},
            {"schema_name": "", "table_name": "clienti", "description": "Anagrafica"},
        ])
        await service.save_scope_annotations(ids["org"], ids["alpha"], ids["vendite"], [
            {"schema_name": "", "table_name": "ordini", "column_name": "", "description": "Ordini"},
            {"schema_name": "vendite", "table_name": "ordini", "column_name": "stato",
             "description": ""},
        ])
        rows = await fetch(
            "SELECT schema_name, table_name, column_name, description FROM db_annotations "
            "ORDER BY table_name, column_name, schema_name"
        )
        other = await service.scope_annotations(ids["org"], ids["alpha"], ids["acquisti"])
        return rows, other

    rows, other = run_db(scenario)
    # La tabella salvata resta solo sullo schema del perimetro, la colonna
    # cancellata non torna, e una tabella non toccata tiene il suo jolly.
    assert rows == [
        {"schema_name": "", "table_name": "clienti", "column_name": "",
         "description": "Anagrafica"},
        {"schema_name": "vendite", "table_name": "ordini", "column_name": "",
         "description": "Ordini"},
    ]
    # L'altro perimetro della stessa connessione non vede più quella tabella.
    assert [(a["schema_name"], a["table_name"]) for a in other] == [("", "clienti")]


def test_inferred_scope_keeps_the_connection_wide_annotations(pg_database):
    async def scenario():
        ids = await seed_two_projects()
        await service.upsert_annotations(ids["crm"], [
            {"schema_name": "altro", "table_name": "note", "description": "Note"},
        ])
        await service.save_scope_annotations(ids["org"], ids["alpha"], ids["crm_alpha"], [
            {"schema_name": "", "table_name": "clienti", "column_name": "", "description": "Clienti"},
        ])
        return await service.scope_annotations(ids["org"], ids["alpha"], ids["crm_alpha"])

    seen = run_db(scenario)
    assert [(a["schema_name"], a["table_name"]) for a in seen] == [("", "clienti"), ("altro", "note")]


def test_query_log_shows_the_schema_of_each_query(pg_database):
    async def scenario():
        ids = await seed_two_projects()
        record = await service.get_scope(ids["org"], ids["alpha"], ids["vendite"])
        await service._log_query(record, ids["org"], ids["alpha"], "ui", "SELECT 1 LIMIT 1",
                                 True, None, 1, 2)
        return await service.query_log(ids["org"], ids["alpha"], ids["erp"])

    log = run_db(scenario)
    assert log[0]["schema_name"] == "vendite" and log[0]["sql_text"] == "SELECT 1 LIMIT 1"


def test_query_refused_for_scope_reasons_is_logged_as_failed(pg_database, monkeypatch):
    """Un rifiuto per perimetro finisce nel log come esito fallito, senza mai
    aprire l'engine sulla connessione remota."""

    async def fail_if_reached(record):
        raise AssertionError("_resolve_engine non doveva essere chiamata su una query fuori perimetro")

    monkeypatch.setattr(service, "_resolve_engine", fail_if_reached)

    async def scenario():
        ids = await seed_two_projects()
        with pytest.raises(ScopeViolationError) as exc:
            await service.run_query(ids["org"], ids["alpha"], ids["vendite"],
                                    "SELECT * FROM acquisti.ordini")
        rows = await fetch(
            "SELECT success, error_message, rows_returned, duration_ms, schema_name "
            "FROM db_query_log WHERE connection_id=$1",
            ids["erp"],
        )
        return str(exc.value), rows

    message, rows = run_db(scenario)
    assert rows == [{
        "success": False, "error_message": message, "rows_returned": 0,
        "duration_ms": 0, "schema_name": "vendite",
    }]


def test_query_refused_for_catalog_reasons_is_logged_as_failed(pg_database, monkeypatch):
    """Lo stesso vale per il rifiuto dei cataloghi di sistema, che non passa
    da ScopeViolationError ma resta un QueryValidationError."""

    async def fail_if_reached(record):
        raise AssertionError("_resolve_engine non doveva essere chiamata su una lettura di catalogo")

    monkeypatch.setattr(service, "_resolve_engine", fail_if_reached)

    async def scenario():
        ids = await seed_two_projects()
        with pytest.raises(QueryValidationError) as exc:
            await service.run_query(ids["org"], ids["alpha"], ids["vendite"],
                                    "SELECT * FROM information_schema.tables")
        rows = await fetch(
            "SELECT success, error_message, rows_returned, duration_ms, schema_name "
            "FROM db_query_log WHERE connection_id=$1",
            ids["erp"],
        )
        return str(exc.value), rows

    message, rows = run_db(scenario)
    assert rows == [{
        "success": False, "error_message": message, "rows_returned": 0,
        "duration_ms": 0, "schema_name": "vendite",
    }]


def test_find_project_scope_compares_the_canonical_scope(pg_database):
    async def scenario():
        ids = await seed_two_projects()
        return ids, (
            await service.find_project_scope(ids["org"], ids["alpha"], ids["erp"], "app", "vendite"),
            # Su PostgreSQL lo schema mancante è public, come in normalize_scope.
            await service.find_project_scope(ids["org"], ids["alpha"], ids["crm"], "crm", None),
            await service.find_project_scope(ids["org"], ids["alpha"], ids["erp"], "app", "public"),
            await service.find_project_scope(ids["org"], ids["beta"], ids["erp"], "app", "vendite"),
            await service.find_project_scope(ids["org"], ids["alpha"], ids["erp"], None, "vendite"),
        )

    ids, (vendite, crm, missing, other_project, malformed) = run_db(scenario)
    assert vendite["scope_id"] == ids["vendite"]
    assert crm["scope_id"] == ids["crm_alpha"]
    assert missing is None and other_project is None and malformed is None


def test_write_refused_for_scope_reasons_is_logged_as_failed(pg_database, monkeypatch):
    """Lo stesso vale per il rifiuto in scrittura."""

    async def fail_if_reached(record):
        raise AssertionError("_resolve_engine non doveva essere chiamata su una scrittura fuori perimetro")

    monkeypatch.setattr(service, "_resolve_engine", fail_if_reached)

    async def scenario():
        ids = await seed_two_projects()
        with pytest.raises(ScopeViolationError) as exc:
            await service.run_write(ids["org"], ids["alpha"], ids["vendite"],
                                    "UPDATE acquisti.ordini SET a = 1 WHERE id = 1")
        rows = await fetch(
            "SELECT success, error_message, rows_returned, duration_ms, schema_name "
            "FROM db_query_log WHERE connection_id=$1",
            ids["erp"],
        )
        return str(exc.value), rows

    message, rows = run_db(scenario)
    assert rows == [{
        "success": False, "error_message": message, "rows_returned": 0,
        "duration_ms": 0, "schema_name": "vendite",
    }]
