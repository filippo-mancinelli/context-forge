"""Authentication and setup security helpers."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from ..db import get_pool

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


async def create_admin_user(username: str, password: str) -> None:
    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)
    pool = await get_pool()
    async with pool.acquire() as conn:
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


async def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(48)
    token_hash = hash_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES ($1, $2, $3)",
            token_hash,
            user_id,
            expires_at,
        )
    return token


async def delete_session(token: str) -> None:
    token_hash = hash_token(token)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM auth_sessions WHERE token_hash = $1", token_hash)


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


async def require_valid_token_or_raise(auth_header: Optional[str]) -> None:
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if not await validate_session_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def create_mcp_api_key(name: str, scope: str = "read,write", created_by: Optional[int] = None, expires_days: Optional[int] = None) -> str:
    """Create a new MCP API key and return the raw key (only shown once)."""
    from datetime import timedelta

    raw_key = f"forge_{secrets.token_urlsafe(36)}"
    key_hash = hash_token(raw_key)

    expires_at = None
    if expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO mcp_api_keys (name, key_hash, scope, created_by, expires_at)
               VALUES ($1, $2, $3, $4, $5)""",
            name,
            key_hash,
            scope,
            created_by,
            expires_at,
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
            """SELECT id, name, scope, expires_at
               FROM mcp_api_keys
               WHERE key_hash = $1 AND (expires_at IS NULL OR expires_at > $2)""",
            key_hash,
            now,
        )

        if row:
            # Update last_used_at
            await conn.execute(
                "UPDATE mcp_api_keys SET last_used_at = $1 WHERE id = $2",
                now,
                row["id"],
            )

    return dict(row) if row else None


async def list_mcp_api_keys(user_id: Optional[int] = None) -> list[dict]:
    """List all MCP API keys (optionally filtered by creator)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id:
            rows = await conn.fetch(
                """SELECT id, name, scope, created_at, last_used_at, expires_at, created_by
                   FROM mcp_api_keys WHERE created_by = $1
                   ORDER BY created_at DESC""",
                user_id,
            )
        else:
            rows = await conn.fetch(
                """SELECT id, name, scope, created_at, last_used_at, expires_at, created_by
                   FROM mcp_api_keys ORDER BY created_at DESC"""
            )
    return [dict(row) for row in rows]


async def revoke_mcp_api_key(key_id: int) -> bool:
    """Revoke an MCP API key by ID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM mcp_api_keys WHERE id = $1", key_id)
        return result == "DELETE 1"


# ========== OAuth 2.0 Functions ==========

async def create_oauth_client(client_id: str, name: str, redirect_uris: list[str], scopes: str = "read,write") -> None:
    """Create a new OAuth client."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO oauth_clients (id, client_id, name, redirect_uris, scopes)
               VALUES ($1, $2, $3, $4, $5)""",
            client_id,
            client_id,
            name,
            redirect_uris,
            scopes,
        )


async def get_oauth_client(client_id: str) -> Optional[dict]:
    """Get OAuth client by ID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM oauth_clients WHERE client_id = $1", client_id)
    return dict(row) if row else None


async def create_authorization_code(client_id: str, user_id: int, redirect_uri: str, scope: str, ttl_seconds: int = 600) -> str:
    """Create an OAuth authorization code."""
    import secrets

    code = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO oauth_authorization_codes (code, client_id, user_id, redirect_uri, scope, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            code,
            client_id,
            user_id,
            redirect_uri,
            scope,
            expires_at,
        )

    return code


async def validate_authorization_code(code: str, client_id: str) -> Optional[dict]:
    """Validate and consume an authorization code."""
    now = datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM oauth_authorization_codes
               WHERE code = $1 AND client_id = $2 AND expires_at > $3""",
            code,
            client_id,
            now,
        )
        if not row:
            return None

        # Delete the code to prevent reuse
        await conn.execute("DELETE FROM oauth_authorization_codes WHERE code = $1", code)

    return dict(row)


async def create_oauth_token(client_id: str, user_id: int, scope: str, ttl_hours: int = 24) -> str:
    """Create an OAuth access token."""
    import secrets

    access_token = secrets.token_urlsafe(48)
    refresh_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO oauth_tokens (access_token, refresh_token, client_id, user_id, scope, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            access_token,
            refresh_token,
            client_id,
            user_id,
            scope,
            expires_at,
        )

    return access_token


async def validate_oauth_token(access_token: str) -> Optional[dict]:
    """Validate an OAuth access token and return token info."""
    now = datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM oauth_tokens
               WHERE access_token = $1 AND expires_at > $2""",
            access_token,
            now,
        )

    return dict(row) if row else None


async def refresh_oauth_token(refresh_token: str) -> Optional[tuple[str, str]]:
    """Refresh an OAuth token using refresh token. Returns (new_access_token, new_refresh_token)."""
    import secrets

    now = datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT client_id, user_id, scope FROM oauth_tokens
               WHERE refresh_token = $1""",
            refresh_token,
        )
        if not row:
            return None

        # Delete old token
        await conn.execute("DELETE FROM oauth_tokens WHERE refresh_token = $1", refresh_token)

        # Create new tokens
        new_access = secrets.token_urlsafe(48)
        new_refresh = secrets.token_urlsafe(48)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

        await conn.execute(
            """INSERT INTO oauth_tokens (access_token, refresh_token, client_id, user_id, scope, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            new_access,
            new_refresh,
            row["client_id"],
            row["user_id"],
            row["scope"],
            expires_at,
        )

    return (new_access, new_refresh)
