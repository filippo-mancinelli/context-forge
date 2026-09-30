"""Selezione delle risorse del catalogo da parte dei progetti.

Una selezione dice che un progetto conosce una risorsa dell'organizzazione: i
tool e le pagine del progetto vedono solo le risorse selezionate. Selezionare
equivale a ottenere accesso, quindi una risorsa riservata la seleziona solo chi
ha il diritto di farlo per conto dell'organizzazione.
"""
from __future__ import annotations

from typing import Any, Optional

from ..db import get_pool

# I nomi di tabella e colonna arrivano solo da qui, mai dall'input.
KINDS: dict[str, dict[str, str]] = {
    "folders": {"table": "ssh_sources", "link": "project_ssh_sources", "column": "ssh_source_id"},
    # Il collegamento di un progetto a un database è il suo perimetro.
    "databases": {"table": "db_connections", "link": "project_db_scopes", "column": "db_connection_id"},
    "repos": {"table": "repos", "link": "project_repos", "column": "repo_id"},
}


class UnknownKindError(ValueError):
    pass


class ResourceNotFoundError(Exception):
    pass


class RestrictedResourceError(Exception):
    pass


def kind_spec(kind: str) -> dict[str, str]:
    spec = KINDS.get(kind)
    if spec is None:
        raise UnknownKindError(f"Unknown resource kind '{kind}'. Use one of: {', '.join(KINDS)}")
    return spec


async def find_resource_by_name(org_id: int, kind: str, name: str) -> Optional[dict[str, Any]]:
    spec = kind_spec(kind)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT id, name, restricted FROM {spec['table']} WHERE org_id = $1 AND lower(name) = lower($2)",
            org_id, name,
        )
    return dict(row) if row else None


async def select_resource(
    org_id: int,
    project_id: int,
    kind: str,
    resource_id: int,
    user_id: Optional[int],
    can_select_restricted: bool,
) -> dict[str, Any]:
    """Aggiunge la risorsa al progetto. Selezionare di nuovo una risorsa già
    presente non è un errore; una risorsa riservata richiede il diritto esplicito.
    Per un database la selezione crea il perimetro predefinito: gli altri
    perimetri della stessa connessione si aggiungono dal progetto."""
    spec = kind_spec(kind)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            project_in_org = await conn.fetchval(
                "SELECT 1 FROM projects WHERE id = $1 AND org_id = $2", project_id, org_id
            )
            resource = await conn.fetchrow(
                f"SELECT id, name, restricted FROM {spec['table']} WHERE id = $1 AND org_id = $2",
                resource_id, org_id,
            )
            if not project_in_org or resource is None:
                raise ResourceNotFoundError(f"{kind} #{resource_id}")
            already = await conn.fetchval(
                f"SELECT 1 FROM {spec['link']} WHERE project_id = $1 AND {spec['column']} = $2 LIMIT 1",
                project_id, resource_id,
            )
            if not already:
                if resource["restricted"] and not can_select_restricted:
                    raise RestrictedResourceError(resource["name"])
                if kind == "databases":
                    inserted = await _ensure_default_scope(conn, project_id, resource_id, user_id)
                else:
                    inserted = await conn.fetchval(
                        f"INSERT INTO {spec['link']} (project_id, {spec['column']}, added_by) "
                        f"VALUES ($1, $2, $3) "
                        f"ON CONFLICT (project_id, {spec['column']}) DO NOTHING "
                        f"RETURNING project_id",
                        project_id, resource_id, user_id,
                    )
                already = inserted is None
    return {"kind": kind, "resource_id": resource_id, "name": resource["name"], "already_selected": bool(already)}


async def deselect_resource(org_id: int, project_id: int, kind: str, resource_id: int) -> bool:
    """Toglie la risorsa dal progetto. Per un database spariscono tutti i
    perimetri di quella connessione nel progetto."""
    spec = kind_spec(kind)
    pool = await get_pool()
    async with pool.acquire() as conn:
        removed = await conn.fetch(
            f"""
            DELETE FROM {spec['link']} l
             USING projects p
             WHERE l.project_id = $1 AND l.{spec['column']} = $2
               AND p.id = l.project_id AND p.org_id = $3
            RETURNING l.project_id
            """,
            project_id, resource_id, org_id,
        )
    return bool(removed)


async def selected_ids(project_id: int, kind: str) -> set[int]:
    spec = kind_spec(kind)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {spec['column']} AS id FROM {spec['link']} WHERE project_id = $1", project_id
        )
    return {int(r["id"]) for r in rows}


async def projects_using(org_id: int, kind: str, resource_id: int) -> list[dict[str, Any]]:
    """Progetti che usano la risorsa, per la conferma di cancellazione. Per un
    database ogni voce è un perimetro: lo stesso progetto compare una volta per
    ciascun perimetro che ha su quella connessione."""
    spec = kind_spec(kind)
    if kind == "databases":
        return await _database_scopes_using(org_id, resource_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT p.id, p.name, p.slug
              FROM {spec['link']} l
              JOIN projects p ON p.id = l.project_id
             WHERE l.{spec['column']} = $1 AND p.org_id = $2
             ORDER BY lower(p.name)
            """,
            resource_id, org_id,
        )
    return [dict(r) for r in rows]


async def _ensure_default_scope(
    conn, project_id: int, connection_id: int, user_id: Optional[int]
) -> Optional[int]:
    """Crea il perimetro predefinito del collegamento a un database.

    Il perimetro nasce dal database della connessione ed è marcato come
    dedotto, finché nessuno lo conferma: chi vuole un altro schema lo cambia
    dal progetto. L'alias parte dal nome della connessione e prende un
    suffisso se nel progetto è già occupato. Ritorna l'id del perimetro creato,
    o ``None`` se il progetto ha già quel perimetro.
    """
    from ..datasources.scopes import ScopeShapeError, default_scope
    from .names import unique_name

    connection = await conn.fetchrow(
        "SELECT name, engine, database_name FROM db_connections WHERE id = $1", connection_id
    )
    if connection is None:
        return None
    try:
        database, schema = default_scope(dict(connection))
    except ScopeShapeError:
        # Senza database non si deduce un perimetro: lo sceglie una persona.
        database, schema = None, None
    taken = {
        r["alias"] for r in await conn.fetch(
            "SELECT lower(alias) AS alias FROM project_db_scopes WHERE project_id = $1", project_id
        )
    }
    return await conn.fetchval(
        """
        INSERT INTO project_db_scopes
            (project_id, db_connection_id, database_name, schema_name, alias, added_by,
             scope_inferred)
        VALUES ($1, $2, $3, $4, $5, $6, true)
        -- Il conflitto va mirato sull'unicità del perimetro: un ON CONFLICT
        -- generico assorbirebbe anche una collisione sull'alias e il
        -- collegamento resterebbe senza perimetro, senza errore.
        ON CONFLICT (project_id, db_connection_id, coalesce(database_name, ''), coalesce(schema_name, ''))
            DO NOTHING
        RETURNING id
        """,
        project_id, connection_id, database, schema,
        unique_name(connection["name"], taken), user_id,
    )


async def _database_scopes_using(org_id: int, connection_id: int) -> list[dict[str, Any]]:
    """Perimetri dei progetti su una connessione, con l'etichetta del perimetro
    effettivo: un perimetro dedotto segue il database attuale della connessione."""
    from ..datasources.scopes import scope_label
    from ..datasources.service import _effective_scope

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id AS project_id, p.name AS project_name, s.id AS scope_id, s.alias,
                   c.engine, c.database_name, s.database_name AS scope_database,
                   s.schema_name AS scope_schema, s.scope_inferred
              FROM project_db_scopes s
              JOIN projects p ON p.id = s.project_id
              JOIN db_connections c ON c.id = s.db_connection_id
             WHERE s.db_connection_id = $1 AND p.org_id = $2
             ORDER BY lower(p.name), lower(s.alias)
            """,
            connection_id, org_id,
        )
    return [
        {
            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "scope_id": r["scope_id"],
            "alias": r["alias"],
            "scope_label": scope_label(r["engine"], *_effective_scope(dict(r))),
        }
        for r in rows
    ]
