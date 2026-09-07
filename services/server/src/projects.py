"""Progetti: contenitori di risorse sotto l'organizzazione.

Ogni progetto espone un endpoint MCP dedicato ``/mcp/{org_slug}/{project_slug}``
e possiede un ``memory_namespace`` proprio che partiziona Mem0. Le risorse
(repo, knowledge base, web, datasource, contratti, chat, job) appartengono a
un solo progetto; utenti e ruoli restano a livello organizzazione.
"""
from __future__ import annotations

from typing import Optional

from .db import get_pool
from .tenancy import _slugify, get_membership_role, role_at_least

# Ruolo org da cui in su si accede a tutti i progetti dell'org senza membership
# esplicito sul singolo progetto.
_PROJECT_SUPERUSER_ROLE = "admin"

DEFAULT_PROJECT_SLUG = "default"
# Collidono con i path riservati del middleware MCP (/oauth/, /health).
RESERVED_PROJECT_SLUGS = frozenset({"oauth", "health"})


def project_namespace(org_slug: str, project_slug: str) -> str:
    """Namespace Mem0 per un progetto."""
    return f"{org_slug}--{project_slug}"


async def _unique_project_slug(conn, org_id: int, base: str) -> str:
    slug = base
    suffix = 1
    while await conn.fetchval(
        "SELECT 1 FROM projects WHERE org_id = $1 AND slug = $2", org_id, slug
    ):
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


async def create_project(org_id: int, name: str) -> dict:
    base_slug = _slugify(name)
    if base_slug in RESERVED_PROJECT_SLUGS:
        raise ValueError(f"Project slug '{base_slug}' is reserved")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            org_slug = await conn.fetchval(
                "SELECT slug FROM organizations WHERE id = $1", org_id
            )
            if org_slug is None:
                raise ValueError(f"Organization {org_id} not found")
            slug = await _unique_project_slug(conn, org_id, base_slug)
            namespace = project_namespace(org_slug, slug)
            row = await conn.fetchrow(
                """INSERT INTO projects (org_id, name, slug, memory_namespace)
                   VALUES ($1, $2, $3, $4)
                   RETURNING id, org_id, name, slug, memory_namespace, created_at""",
                org_id,
                name,
                slug,
                namespace,
            )
    return dict(row)


async def get_project(project_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, org_id, name, slug, memory_namespace, created_at
               FROM projects WHERE id = $1""",
            project_id,
        )
    return dict(row) if row else None


async def list_projects(org_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, org_id, name, slug, memory_namespace, created_at
               FROM projects WHERE org_id = $1 ORDER BY created_at""",
            org_id,
        )
    return [dict(r) for r in rows]


async def update_project(project_id: int, name: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE projects SET name = $2 WHERE id = $1
               RETURNING id, org_id, name, slug, memory_namespace, created_at""",
            project_id,
            name,
        )
    return dict(row) if row else None


async def delete_project(project_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM projects WHERE id = $1", project_id)
    return result == "DELETE 1"


async def resolve_project_by_slugs(org_slug: str, project_slug: str) -> Optional[dict]:
    """Risolve org+progetto dagli slug del path MCP."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT p.id, p.org_id, p.name, p.slug, p.memory_namespace,
                      o.slug AS org_slug
               FROM projects p
               JOIN organizations o ON o.id = p.org_id
               WHERE o.slug = $1 AND p.slug = $2""",
            org_slug,
            project_slug,
        )
    return dict(row) if row else None


async def get_default_project_id(org_id: int) -> Optional[int]:
    """Il progetto più vecchio dell'org (quello creato dalla migrazione)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT id FROM projects WHERE org_id = $1 ORDER BY created_at, id LIMIT 1",
            org_id,
        )
    return int(val) if val is not None else None


async def count_projects(org_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM projects WHERE org_id = $1", org_id
            )
            or 0
        )


# Tabelle che portano project_id e che rendono "occupato" un progetto. Non sono
# incluse project_members e mcp_api_key_projects: hanno già ON DELETE CASCADE
# verso projects e spariscono da sole.
PROJECT_RESOURCE_TABLES = (
    "repos",
    "kb_documents",
    "web_sites",
    "db_connections",
    "api_contracts",
    "ssh_sources",
    "chat_sessions",
    "jobs",
    "mcp_api_keys",
)


async def count_project_resources(project_id: int) -> dict[str, int]:
    """Quante risorse occupano il progetto, per tipo (solo i tipi con almeno una).

    Serve a decidere se un progetto è cancellabile: le tabelle scoped portano
    project_id come semplice colonna, senza foreign key, quindi cancellare un
    progetto pieno lascerebbe righe orfane irraggiungibili.
    """
    counts: dict[str, int] = {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        for table in PROJECT_RESOURCE_TABLES:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM {table} WHERE project_id = $1", project_id
            )
            if total:
                counts[table] = int(total)
    return counts


async def bind_repo_to_project(org_id: int, project_id: int, repo_name: str) -> None:
    """Lega un repo al progetto indicato.

    Il config di org è bootstrap org-level: le righe nate dal sync finiscono nel
    progetto di default, quindi chi aggiunge un repo da un progetto specifico
    deve riassegnarlo esplicitamente.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE repos SET project_id = $1 WHERE org_id = $2 AND name = $3",
            project_id,
            org_id,
            repo_name,
        )


async def get_repo_project_name(org_id: int, repo_name: str) -> Optional[str]:
    """Nome del progetto che contiene il repo, se il repo esiste già."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            """
            SELECT p.name FROM repos r
            JOIN projects p ON p.id = r.project_id
            WHERE r.org_id = $1 AND r.name = $2
            """,
            org_id,
            repo_name,
        )
    return str(val) if val is not None else None


# ── Abilitazione utente↔progetto ──────────────────────────────────────────────
# owner/admin dell'org accedono a tutti i progetti (ruolo effettivo = ruolo org);
# member/viewer solo dove esiste un membership esplicito in project_members
# (ruolo effettivo = quello del membership).


async def resolve_project_access(
    org_id: int, user_id: int, project_id: int
) -> Optional[str]:
    """Ruolo effettivo dell'utente su un progetto, o None se non vi ha accesso.

    None copre entrambi i casi: non membro dell'org, o membro senza abilitazione
    esplicita su questo progetto.
    """
    org_role = await get_membership_role(org_id, user_id)
    if org_role is None:
        return None
    if role_at_least(org_role, _PROJECT_SUPERUSER_ROLE):
        return org_role
    pool = await get_pool()
    async with pool.acquire() as conn:
        prole = await conn.fetchval(
            """
            SELECT pm.role FROM project_members pm
            JOIN projects p ON p.id = pm.project_id
            WHERE pm.project_id = $1 AND pm.user_id = $2 AND p.org_id = $3
            """,
            project_id,
            user_id,
            org_id,
        )
    return str(prole) if prole is not None else None


async def list_accessible_projects(org_id: int, user_id: int) -> list[dict]:
    """Progetti dell'org visibili all'utente, ciascuno col ruolo effettivo."""
    org_role = await get_membership_role(org_id, user_id)
    if org_role is None:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        if role_at_least(org_role, _PROJECT_SUPERUSER_ROLE):
            rows = await conn.fetch(
                """
                SELECT id, org_id, name, slug, memory_namespace, created_at
                FROM projects WHERE org_id = $1 ORDER BY created_at, id
                """,
                org_id,
            )
            return [{**dict(r), "role": org_role} for r in rows]
        rows = await conn.fetch(
            """
            SELECT p.id, p.org_id, p.name, p.slug, p.memory_namespace,
                   p.created_at, pm.role
            FROM project_members pm
            JOIN projects p ON p.id = pm.project_id
            WHERE p.org_id = $1 AND pm.user_id = $2
            ORDER BY p.created_at, p.id
            """,
            org_id,
            user_id,
        )
    return [dict(r) for r in rows]


async def list_project_members(project_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT pm.user_id, pm.role, pm.created_at, u.username, u.email
            FROM project_members pm
            JOIN admin_users u ON u.id = pm.user_id
            WHERE pm.project_id = $1
            ORDER BY u.username, pm.user_id
            """,
            project_id,
        )
    return [dict(r) for r in rows]


async def add_project_member(project_id: int, user_id: int, role: str = "member") -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO project_members (project_id, user_id, role)
            VALUES ($1, $2, $3)
            ON CONFLICT (project_id, user_id) DO UPDATE SET role = EXCLUDED.role
            """,
            project_id,
            user_id,
            role,
        )


async def remove_project_member(project_id: int, user_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM project_members WHERE project_id = $1 AND user_id = $2 RETURNING user_id",
            project_id,
            user_id,
        )
    return deleted is not None
