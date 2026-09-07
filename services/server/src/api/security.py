"""Authentication and setup security helpers."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from ..datasources.secrets import SecretError, decrypt_secret, encrypt_secret
from ..db import get_pool
from ..mcp.permissions import permissions_from_scope

PBKDF2_ITERATIONS = 240_000
SESSION_TTL_HOURS = 24


def _hash_password(password: str, salt: str) -> str:
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return raw.hex()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def has_admin_user() -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM admin_users")
    return bool(count)


async def has_runtime_config() -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM app_runtime_config WHERE id = 1")
    return row is not None


async def is_configured() -> bool:
    return await has_admin_user() and await has_runtime_config()


async def create_admin_user(username: str, password: str, email: Optional[str] = None) -> int:
    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "INSERT INTO admin_users (username, password_hash, salt, email) "
                "VALUES ($1, $2, $3, $4) RETURNING id",
                username,
                password_hash,
                salt,
                email.lower().strip() if email else None,
            )
        )


async def get_admin_user(user_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, email, created_at FROM admin_users WHERE id = $1",
            user_id,
        )
    return dict(row) if row else None


async def get_user_id_by_email(email: str) -> Optional[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM admin_users WHERE lower(email) = lower($1)", email
        )


async def reset_admin_password(username: str, new_password: str) -> None:
    """Reset password for an existing admin user, or create if username doesn't exist."""
    salt = secrets.token_hex(16)
    password_hash = _hash_password(new_password, salt)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE admin_users SET password_hash = $1, salt = $2 WHERE username = $3",
            password_hash,
            salt,
            username,
        )
        if result == "UPDATE 0":
            await conn.execute(
                "INSERT INTO admin_users (username, password_hash, salt) VALUES ($1, $2, $3)",
                username,
                password_hash,
                salt,
            )


async def authenticate_admin(username: str, password: str) -> Optional[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, password_hash, salt FROM admin_users WHERE username = $1",
            username,
        )
    if not row:
        return None

    expected = row["password_hash"]
    computed = _hash_password(password, row["salt"])
    if not hmac.compare_digest(expected, computed):
        return None
    return int(row["id"])


async def create_session(user_id: int, id_token: Optional[str] = None) -> str:
    token = secrets.token_urlsafe(48)
    token_hash = hash_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    stored_id_token = encrypt_secret(id_token) if id_token is not None else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, expires_at, oidc_id_token) VALUES ($1, $2, $3, $4)",
            token_hash,
            user_id,
            expires_at,
            stored_id_token,
        )
    return token


async def delete_session(token: str) -> Optional[str]:
    """Delete session; return the decrypted OIDC id_token associated with it (None if password login)."""
    token_hash = hash_token(token)
    pool = await get_pool()
    async with pool.acquire() as conn:
        stored_id_token = await conn.fetchval(
            "DELETE FROM auth_sessions WHERE token_hash = $1 RETURNING oidc_id_token",
            token_hash,
        )
    if stored_id_token is None:
        return None
    try:
        return decrypt_secret(stored_id_token)
    except SecretError:
        # Undecryptable (e.g. wrong/rotated ENCRYPTION_KEY). Logout must still
        # succeed locally; the id_token just won't be forwarded to the IdP.
        return None


async def validate_session_token(token: str) -> bool:
    token_hash = hash_token(token)
    now = datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT token_hash FROM auth_sessions WHERE token_hash = $1 AND expires_at > $2",
            token_hash,
            now,
        )
        await conn.execute("DELETE FROM auth_sessions WHERE expires_at <= $1", now)
    return row is not None


async def resolve_session_user(token: str) -> Optional[int]:
    """Return the user_id for a valid, unexpired session token (else None)."""
    token_hash = hash_token(token)
    now = datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id FROM auth_sessions WHERE token_hash = $1 AND expires_at > $2",
            token_hash,
            now,
        )
    return int(row["user_id"]) if row else None


async def require_valid_token_or_raise(auth_header: Optional[str]) -> None:
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if not await validate_session_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def create_mcp_api_key(
    name: str,
    scope: str = "read,write",
    created_by: Optional[int] = None,
    expires_days: Optional[int] = None,
    org_id: Optional[int] = None,
    permissions: Optional[str] = None,
    project_id: Optional[int] = None,
    project_ids: Optional[list[int]] = None,
) -> str:
    """Create a new MCP API key and return the raw key (only shown once).

    ``project_ids`` è l'elenco dei progetti su cui la key è abilitata (key
    org-level). Lista vuota + ``project_id`` None = key valida su tutti i
    progetti dell'org. ``project_id`` singolo resta accettato come alias legacy.
    """
    from datetime import timedelta

    raw_key = f"forge_{secrets.token_urlsafe(36)}"
    key_hash = hash_token(raw_key)

    expires_at = None
    if expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

    if permissions is None:
        permissions = ",".join(sorted(permissions_from_scope(scope)))

    allowed = list(dict.fromkeys(project_ids or ([] if project_id is None else [project_id])))
    # project_id legacy: primo dei consentiti, se presente.
    legacy_project_id = allowed[0] if allowed else None

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            key_id = await conn.fetchval(
                """INSERT INTO mcp_api_keys (name, key_hash, scope, created_by, expires_at, org_id, permissions, project_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id""",
                name,
                key_hash,
                scope,
                created_by,
                expires_at,
                org_id,
                permissions,
                legacy_project_id,
            )
            for pid in allowed:
                await conn.execute(
                    """INSERT INTO mcp_api_key_projects (key_id, project_id)
                       VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                    key_id,
                    pid,
                )

    return raw_key


async def validate_mcp_api_key(api_key: str) -> Optional[dict]:
    """Validate an MCP API key and return key info if valid."""
    if not api_key or not api_key.startswith("forge_"):
        return None

    key_hash = hash_token(api_key)
    now = datetime.now(timezone.utc)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, name, scope, permissions, expires_at, org_id, project_id,
                      rate_limit_per_minute
               FROM mcp_api_keys
               WHERE key_hash = $1 AND (expires_at IS NULL OR expires_at > $2)""",
            key_hash,
            now,
        )

        if not row:
            return None

        # Progetti consentiti dalla key (tabella ponte). Vuoto = nessun vincolo
        # esplicito: il chiamante ricade sul project_id legacy o su org-wide.
        allowed_rows = await conn.fetch(
            "SELECT project_id FROM mcp_api_key_projects WHERE key_id = $1",
            row["id"],
        )
        # Update last_used_at
        await conn.execute(
            "UPDATE mcp_api_keys SET last_used_at = $1 WHERE id = $2",
            now,
            row["id"],
        )

    info = dict(row)
    info["allowed_projects"] = [r["project_id"] for r in allowed_rows]
    return info


async def list_mcp_api_keys(org_id: Optional[int] = None) -> list[dict]:
    """List MCP API keys, optionally scoped to a single organization."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if org_id is not None:
            rows = await conn.fetch(
                """SELECT k.id, k.name, k.scope, k.permissions, k.created_at, k.last_used_at,
                          k.expires_at, k.created_by, k.org_id, k.project_id, p.slug AS project_slug
                   FROM mcp_api_keys k
                   LEFT JOIN projects p ON p.id = k.project_id
                   WHERE k.org_id = $1
                   ORDER BY k.created_at DESC""",
                org_id,
            )
        else:
            rows = await conn.fetch(
                """SELECT k.id, k.name, k.scope, k.permissions, k.created_at, k.last_used_at,
                          k.expires_at, k.created_by, k.org_id, k.project_id, p.slug AS project_slug
                   FROM mcp_api_keys k
                   LEFT JOIN projects p ON p.id = k.project_id
                   ORDER BY k.created_at DESC"""
            )
    return [dict(row) for row in rows]


async def get_mcp_api_key(key_id: int) -> Optional[dict]:
    """Fetch a single API key's metadata (for ownership checks)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, scope, permissions, created_by, org_id FROM mcp_api_keys WHERE id = $1",
            key_id,
        )
    return dict(row) if row else None


async def revoke_mcp_api_key(key_id: int, org_id: Optional[int] = None) -> bool:
    """Revoke an MCP API key by ID, optionally constrained to an organization."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if org_id is not None:
            result = await conn.execute(
                "DELETE FROM mcp_api_keys WHERE id = $1 AND org_id = $2", key_id, org_id
            )
        else:
            result = await conn.execute("DELETE FROM mcp_api_keys WHERE id = $1", key_id)
        return result == "DELETE 1"


async def find_or_create_oidc_user(claims: dict) -> int:
    """Find-or-create a local user for a Keycloak identity (no org membership side effects).

    Safe to call from any authenticated path, including the MCP request path: it never
    inserts into ``organization_members``, so a user with no membership stays without one.
    """
    sub = claims["sub"]
    username = claims.get("preferred_username") or f"oidc:{sub}"
    email = claims.get("email")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM admin_users WHERE oidc_sub=$1", sub)
        if row:
            user_id = row["id"]
        else:
            existing = await conn.fetchrow("SELECT id FROM admin_users WHERE username=$1", username)
            if existing:
                user_id = existing["id"]
                await conn.execute("UPDATE admin_users SET oidc_sub=$1 WHERE id=$2", sub, user_id)
            else:
                salt = secrets.token_hex(16)
                pw_hash = _hash_password(secrets.token_urlsafe(32), salt)
                user_id = await conn.fetchval(
                    "INSERT INTO admin_users (username, password_hash, salt, email, oidc_sub) "
                    "VALUES ($1,$2,$3,$4,$5) RETURNING id",
                    username, pw_hash, salt, email, sub,
                )
    return user_id


async def provision_oidc_user(claims: dict) -> int:
    """Find-or-create a local user for a Keycloak identity.

    Keycloak only authenticates the user. Organization membership is owned by the
    application (in-app memberships and invitations) and never derived from token
    claims, so this creates the user record without any organization side effects.
    After login the user sees exactly the organizations they have been granted in
    the app; a user with no membership yet has access to none.
    """
    return await find_or_create_oidc_user(claims)
