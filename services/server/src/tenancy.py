"""Multi-tenancy: organizations, memberships and roles.

An organization is the unit of tenant isolation. Each organization owns a
dedicated ``memory_namespace`` (used as the Mem0 ``user_id`` partition) plus
its own API keys, jobs and repositories. Users join organizations through
``organization_members`` with a role that drives RBAC.
"""
from __future__ import annotations

import logging
import re
import secrets
from typing import Optional

from .db import get_pool

logger = logging.getLogger(__name__)

# Role hierarchy. Higher rank implies all capabilities of lower ranks.
ROLES = ("viewer", "member", "admin", "owner")
ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}

DEFAULT_ORG_SLUG = "default"
# Collidono con i path riservati dell'endpoint MCP (/oauth/, /health).
RESERVED_ORG_SLUGS = frozenset({"oauth", "health"})


def role_at_least(role: Optional[str], minimum: str) -> bool:
    """Return True when ``role`` is at least as privileged as ``minimum``."""
    if role is None:
        return False
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(minimum, 999)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "org"


async def _unique_slug(conn, base: str) -> str:
    slug = base
    suffix = 1
    while slug in RESERVED_ORG_SLUGS or await conn.fetchval(
        "SELECT 1 FROM organizations WHERE slug = $1", slug
    ):
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


# ===== Organizations =====

async def create_organization(name: str, owner_user_id: int, namespace: Optional[str] = None) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            slug = await _unique_slug(conn, _slugify(name))
            ns = namespace or f"org_{slug}"
            # Guard against namespace collisions.
            if await conn.fetchval("SELECT 1 FROM organizations WHERE memory_namespace = $1", ns):
                ns = f"org_{slug}_{secrets.token_hex(3)}"
            row = await conn.fetchrow(
                """INSERT INTO organizations (name, slug, memory_namespace)
                   VALUES ($1, $2, $3)
                   RETURNING id, name, slug, memory_namespace, created_at""",
                name,
                slug,
                ns,
            )
            await conn.execute(
                """INSERT INTO organization_members (org_id, user_id, role)
                   VALUES ($1, $2, 'owner')""",
                row["id"],
                owner_user_id,
            )
            await conn.execute(
                """INSERT INTO projects (org_id, name, slug, memory_namespace)
                   VALUES ($1, 'Default', 'default', $2)""",
                row["id"],
                ns,
            )
    return dict(row)


async def get_organization(org_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, slug, memory_namespace, created_at FROM organizations WHERE id = $1",
            org_id,
        )
    return dict(row) if row else None


async def get_organization_by_slug(slug: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, slug, memory_namespace FROM organizations WHERE slug=$1",
            slug,
        )
    return dict(row) if row else None


async def list_organizations_for_user(user_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT o.id, o.name, o.slug, o.memory_namespace, o.created_at, m.role
               FROM organizations o
               JOIN organization_members m ON m.org_id = o.id
               WHERE m.user_id = $1
               ORDER BY o.created_at""",
            user_id,
        )
    return [dict(r) for r in rows]


async def update_organization(org_id: int, name: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE organizations SET name = $2 WHERE id = $1
               RETURNING id, name, slug, memory_namespace, created_at""",
            org_id,
            name,
        )
    return dict(row) if row else None


async def delete_organization(org_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
    return result == "DELETE 1"


async def count_organizations() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT count(*) FROM organizations") or 0)


# ===== Memberships =====

async def get_membership_role(org_id: int, user_id: int) -> Optional[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT role FROM organization_members WHERE org_id = $1 AND user_id = $2",
            org_id,
            user_id,
        )


async def list_members(org_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT m.user_id, m.role, m.created_at, u.username, u.email
               FROM organization_members m
               JOIN admin_users u ON u.id = m.user_id
               WHERE m.org_id = $1
               ORDER BY m.created_at""",
            org_id,
        )
    return [dict(r) for r in rows]


async def add_member(org_id: int, user_id: int, role: str = "member") -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO organization_members (org_id, user_id, role)
               VALUES ($1, $2, $3)
               ON CONFLICT (org_id, user_id) DO UPDATE SET role = EXCLUDED.role""",
            org_id,
            user_id,
            role,
        )


async def count_owners(org_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM organization_members WHERE org_id = $1 AND role = 'owner'",
                org_id,
            )
            or 0
        )


async def update_member_role(org_id: int, user_id: int, role: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE organization_members SET role = $3 WHERE org_id = $1 AND user_id = $2",
            org_id,
            user_id,
            role,
        )
    return result == "UPDATE 1"


async def remove_member(org_id: int, user_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM organization_members WHERE org_id = $1 AND user_id = $2",
            org_id,
            user_id,
        )
    return result == "DELETE 1"


# ===== Default org bootstrap / migration =====

async def ensure_default_org() -> Optional[int]:
    """Ensure a default organization exists and existing data is attributed to it.

    Idempotent. Safe to call on every startup. Returns the default org id, or
    None when there is no admin user yet (fresh install pre-setup).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM organizations ORDER BY id LIMIT 1")
        if existing:
            return int(existing)

        admin_ids = [r["id"] for r in await conn.fetch("SELECT id FROM admin_users ORDER BY id")]
        if not admin_ids:
            return None

        # Keep existing memories visible: reuse the configured default namespace.
        try:
            from .config import get_forge_config

            namespace = get_forge_config().memory.user_id or "default"
        except Exception:
            namespace = "default"

        async with conn.transaction():
            org_id = await conn.fetchval(
                """INSERT INTO organizations (name, slug, memory_namespace)
                   VALUES ($1, $2, $3) RETURNING id""",
                "Default",
                DEFAULT_ORG_SLUG,
                namespace,
            )
            for idx, uid in enumerate(admin_ids):
                await conn.execute(
                    """INSERT INTO organization_members (org_id, user_id, role)
                       VALUES ($1, $2, $3)
                       ON CONFLICT DO NOTHING""",
                    org_id,
                    uid,
                    "owner" if idx == 0 else "admin",
                )
            # Attribute pre-existing tenant-scoped data to the default org.
            await conn.execute("UPDATE repos SET org_id = $1 WHERE org_id IS NULL", org_id)
            await conn.execute("UPDATE jobs SET org_id = $1 WHERE org_id IS NULL", org_id)
            await conn.execute("UPDATE mcp_api_keys SET org_id = $1 WHERE org_id IS NULL", org_id)

        # Seed the default org's config from the existing global bootstrap config
        # so its repositories and indexing settings carry over unchanged.
        try:
            from .config import get_forge_config
            from .org_config import seed_org_config_if_absent

            await seed_org_config_if_absent(int(org_id), get_forge_config())
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Could not seed default org config: %s", e)

        logger.info("Created default organization (id=%s, namespace=%s)", org_id, namespace)
        return int(org_id)


async def ensure_tenant_storage() -> Optional[int]:
    """Ensure the default org exists and tenant-aware storage is migrated.

    Idempotent. Returns the default organization id, or None pre-setup.
    """
    default_org_id = await ensure_default_org()
    if default_org_id is None:
        return None
    from .db import apply_project_migration, apply_tenant_repo_migration

    await apply_tenant_repo_migration(default_org_id)
    await apply_project_migration()
    return default_org_id


async def get_namespace_for_org(org_id: int) -> Optional[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT memory_namespace FROM organizations WHERE id = $1", org_id
        )


# ===== Permessi MCP per ruolo =====

async def get_org_role_permissions(org_id: int) -> dict[str, list[str]]:
    """Righe personalizzate della matrice, raggruppate per ruolo. {} = default."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, permission FROM org_role_permissions "
            "WHERE org_id=$1 ORDER BY role, permission",
            org_id,
        )
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["role"], []).append(row["permission"])
    return out


async def set_org_role_permissions(org_id: int, matrix: dict[str, list[str]]) -> None:
    """Sostituisce l'intera matrice personalizzata dell'organizzazione."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM org_role_permissions WHERE org_id=$1", org_id)
            for role, perms in matrix.items():
                for perm in perms:
                    await conn.execute(
                        "INSERT INTO org_role_permissions (org_id, role, permission) "
                        "VALUES ($1, $2, $3)",
                        org_id,
                        role,
                        perm,
                    )


async def clear_org_role_permissions(org_id: int) -> None:
    """Ripristina i default cancellando le personalizzazioni dell'org."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM org_role_permissions WHERE org_id=$1", org_id)


async def resolve_role_permissions(org_id: Optional[int], role: Optional[str]) -> frozenset:
    """Permessi MCP effettivi per un ruolo in un'org.

    An org with ANY customized rows is fully "in charge" of its matrix: a role that has
    no rows in that case resolves to an explicit ``frozenset()`` (revoked), not the
    hardcoded default. Only an org with zero rows at all falls back to
    ``DEFAULT_ROLE_PERMISSIONS`` — this is what lets an admin actually revoke all
    permissions for a role (see C2) instead of silently keeping the defaults.
    """
    from .mcp.permissions import DEFAULT_ROLE_PERMISSIONS

    if org_id is None or role is None:
        return frozenset()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, permission FROM org_role_permissions WHERE org_id=$1",
            org_id,
        )
    if rows:
        return frozenset(r["permission"] for r in rows if r["role"] == role)
    return DEFAULT_ROLE_PERMISSIONS.get(role, frozenset())
