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
    "databases": {"table": "db_connections", "link": "project_db_connections", "column": "db_connection_id"},
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
    presente non è un errore; una risorsa riservata richiede il diritto esplicito."""
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
                f"SELECT 1 FROM {spec['link']} WHERE project_id = $1 AND {spec['column']} = $2",
                project_id, resource_id,
            )
            if not already:
                if resource["restricted"] and not can_select_restricted:
                    raise RestrictedResourceError(resource["name"])
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
    spec = kind_spec(kind)
    pool = await get_pool()
    async with pool.acquire() as conn:
        removed = await conn.fetchval(
            f"""
            DELETE FROM {spec['link']} l
             USING projects p
             WHERE l.project_id = $1 AND l.{spec['column']} = $2
               AND p.id = l.project_id AND p.org_id = $3
            RETURNING l.project_id
            """,
            project_id, resource_id, org_id,
        )
    return removed is not None


async def selected_ids(project_id: int, kind: str) -> set[int]:
    spec = kind_spec(kind)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {spec['column']} AS id FROM {spec['link']} WHERE project_id = $1", project_id
        )
    return {int(r["id"]) for r in rows}


async def projects_using(org_id: int, kind: str, resource_id: int) -> list[dict[str, Any]]:
    spec = kind_spec(kind)
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
