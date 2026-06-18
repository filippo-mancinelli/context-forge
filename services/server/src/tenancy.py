"""Multi-tenancy: organizations, memberships, roles and invitations.

An organization is the unit of tenant isolation. Each organization owns a
dedicated ``memory_namespace`` (used as the Mem0 ``user_id`` partition) plus
its own API keys, jobs and repositories. Users join organizations through
``organization_members`` with a role that drives RBAC.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import get_pool

logger = logging.getLogger(__name__)

# Role hierarchy. Higher rank implies all capabilities of lower ranks.
ROLES = ("viewer", "member", "admin", "owner")
ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}

DEFAULT_ORG_SLUG = "default"
INVITATION_TTL_DAYS = 7


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
    while await conn.fetchval("SELECT 1 FROM organizations WHERE slug = $1", slug):
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
    return dict(row)


async def get_organization(org_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, slug, memory_namespace, created_at FROM organizations WHERE id = $1",
            org_id,
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


# ===== Invitations =====

def _hash_invite_token(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_invitation(org_id: int, email: str, role: str, invited_by: Optional[int]) -> tuple[str, dict]:
    """Create an invitation and return (raw_token, invitation_record)."""
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_invite_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITATION_TTL_DAYS)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO organization_invitations (org_id, email, role, token_hash, invited_by, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6)
               RETURNING id, org_id, email, role, expires_at, accepted_at, created_at""",
            org_id,
            email.lower().strip(),
            role,
            token_hash,
            invited_by,
            expires_at,
        )
    return raw_token, dict(row)


async def list_invitations(org_id: int, include_accepted: bool = False) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if include_accepted:
            rows = await conn.fetch(
                """SELECT id, org_id, email, role, expires_at, accepted_at, created_at
                   FROM organization_invitations WHERE org_id = $1 ORDER BY created_at DESC""",
                org_id,
            )
        else:
            rows = await conn.fetch(
                """SELECT id, org_id, email, role, expires_at, accepted_at, created_at
                   FROM organization_invitations
                   WHERE org_id = $1 AND accepted_at IS NULL
                   ORDER BY created_at DESC""",
                org_id,
            )
    return [dict(r) for r in rows]


async def revoke_invitation(org_id: int, invitation_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM organization_invitations WHERE id = $1 AND org_id = $2 AND accepted_at IS NULL",
            invitation_id,
            org_id,
        )
    return result == "DELETE 1"


async def get_invitation_by_token(raw_token: str) -> Optional[dict]:
    token_hash = _hash_invite_token(raw_token)
    now = datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT i.id, i.org_id, i.email, i.role, i.expires_at, i.accepted_at, o.name AS org_name
               FROM organization_invitations i
               JOIN organizations o ON o.id = i.org_id
               WHERE i.token_hash = $1 AND i.accepted_at IS NULL AND i.expires_at > $2""",
            token_hash,
            now,
        )
    return dict(row) if row else None


async def mark_invitation_accepted(invitation_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE organization_invitations SET accepted_at = NOW() WHERE id = $1",
            invitation_id,
        )


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

        logger.info("Created default organization (id=%s, namespace=%s)", org_id, namespace)
        return int(org_id)


async def get_namespace_for_org(org_id: int) -> Optional[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT memory_namespace FROM organizations WHERE id = $1", org_id
        )
