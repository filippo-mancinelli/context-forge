import asyncio

import pytest

from src.datasources import service
from src.datasources.scopes import ScopeViolationError
from src.datasources.validator import QueryValidationError

CONFIRMED = {
    "id": 4, "name": "erp", "alias": "erp-vendite", "engine": "postgresql",
    "database_name": "app", "scope_id": 11, "scope_database": "app",
    "scope_schema": "vendite", "scope_inferred": False, "scope_label": "app.vendite",
}
INFERRED = {**CONFIRMED, "alias": "erp", "scope_schema": "public",
            "scope_inferred": True, "scope_label": "app.public"}


def _patch(monkeypatch, record):
    executed = []

    async def fake_get_scope(org_id, project_id, ref, include_secret=False):
        return record

    async def fake_list(org_id, project_id):
        return [{"alias": "erp-acquisti"}, {"alias": "erp-vendite"}]

    async def fake_engine(rec):
        return "engine"

    def fake_read(engine, sql, max_rows):
        executed.append(sql)
        return ["n"], [{"n": 1}], False

    def fake_write(engine, sql):
        executed.append(sql)
        return 1

    async def fake_log(*args):
        return None

    monkeypatch.setattr(service, "get_scope", fake_get_scope)
    monkeypatch.setattr(service, "list_connections", fake_list)
    monkeypatch.setattr(service, "_resolve_engine", fake_engine)
    monkeypatch.setattr(service, "_execute_readonly", fake_read)
    monkeypatch.setattr(service, "_execute_write", fake_write)
    monkeypatch.setattr(service, "_log_query", fake_log)
    return executed


def test_query_outside_a_confirmed_scope_is_refused_before_running(monkeypatch):
    executed = _patch(monkeypatch, CONFIRMED)
    with pytest.raises(ScopeViolationError) as exc:
        asyncio.run(service.run_query(1, 2, 11, "SELECT * FROM acquisti.ordini"))
    message = str(exc.value)
    assert "acquisti.ordini" in message and "app.vendite" in message
    assert "erp-acquisti, erp-vendite" in message and "catalog_list" in message
    assert executed == []


def test_query_inside_the_scope_runs(monkeypatch):
    executed = _patch(monkeypatch, CONFIRMED)
    out = asyncio.run(service.run_query(1, 2, 11, "SELECT * FROM vendite.ordini o WHERE o.id = 1"))
    assert out["rows"] == [{"n": 1}]
    assert executed == ["SELECT * FROM vendite.ordini o WHERE o.id = 1 LIMIT 100"]


def test_write_outside_a_confirmed_scope_is_refused_before_running(monkeypatch):
    executed = _patch(monkeypatch, CONFIRMED)
    with pytest.raises(ScopeViolationError, match="acquisti.ordini"):
        asyncio.run(service.run_write(1, 2, 11, "UPDATE acquisti.ordini SET a = 1 WHERE id = 1"))
    assert executed == []


def test_inferred_scope_is_not_restricted_to_a_schema(monkeypatch):
    executed = _patch(monkeypatch, INFERRED)
    asyncio.run(service.run_query(1, 2, 11, "SELECT * FROM acquisti.ordini"))
    assert executed == ["SELECT * FROM acquisti.ordini LIMIT 100"]


@pytest.mark.parametrize("record", [CONFIRMED, INFERRED])
def test_system_catalogs_are_refused_on_any_scope(monkeypatch, record):
    executed = _patch(monkeypatch, record)
    with pytest.raises(QueryValidationError, match="system catalogs"):
        asyncio.run(service.run_query(1, 2, 11, "SELECT * FROM information_schema.tables"))
    assert executed == []


def test_a_record_missing_scope_inferred_is_treated_as_confirmed(monkeypatch):
    """Una riga vera porta sempre ``scope_inferred``; un record che non la
    porta resta confermato e continua a imporre il proprio schema, invece di
    aprirsi come un perimetro dedotto."""
    record = {k: v for k, v in CONFIRMED.items() if k != "scope_inferred"}
    executed = _patch(monkeypatch, record)
    with pytest.raises(ScopeViolationError):
        asyncio.run(service.run_query(1, 2, 11, "SELECT * FROM acquisti.ordini"))
    assert executed == []


def test_effective_scope_inferred_branch_never_needs_the_engine_key():
    """Il ramo dedotto di ``_effective_scope`` restituisce
    ``(database_name, None)`` senza mai leggere ``engine``: un record parziale
    che dichiara solo di essere dedotto non deve sollevare ``KeyError``."""
    record = {"scope_inferred": True, "database_name": "app"}
    assert service._effective_scope(record) == ("app", None)

def test_a_record_missing_scope_inferred_still_confines_the_requested_schema(monkeypatch):
    """La guardia sullo schema chiesto legge ``scope_inferred`` come
    ``_effective_scope``: un record che non porta la colonna resta confermato e
    rifiuta uno schema diverso, invece di accettarlo."""
    record = {k: v for k, v in CONFIRMED.items() if k != "scope_inferred"}
    _patch(monkeypatch, record)
    with pytest.raises(ScopeViolationError):
        asyncio.run(service._requested_schema(1, 2, record, "acquisti"))
    assert asyncio.run(service._requested_schema(1, 2, record, None)) == "vendite"


def test_the_requested_schema_ignores_the_case_like_the_validator(monkeypatch):
    """I nomi nudi si confrontano senza distinguere le maiuscole, come nel
    validator: lo schema del perimetro scritto con altre maiuscole è lo stesso
    schema, e la richiesta lavora sulla forma del perimetro."""
    _patch(monkeypatch, CONFIRMED)
    assert asyncio.run(service._requested_schema(1, 2, CONFIRMED, "Vendite")) == "vendite"
    assert asyncio.run(service._requested_schema(1, 2, CONFIRMED, "vendite")) == "vendite"
    with pytest.raises(ScopeViolationError):
        asyncio.run(service._requested_schema(1, 2, CONFIRMED, "acquisti"))


def test_an_inferred_scope_keeps_the_requested_schema_as_it_is(monkeypatch):
    """Un perimetro dedotto segue la connessione: lo schema chiesto arriva
    all'introspezione così come è scritto."""
    _patch(monkeypatch, INFERRED)
    assert asyncio.run(service._requested_schema(1, 2, INFERRED, "Acquisti")) == "Acquisti"
