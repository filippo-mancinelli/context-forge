"""Authentication and setup security helpers."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from ..config import get_forge_config
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


async def is_legacy_mode_available() -> bool:
    """Allow existing file-based installs to run without forced setup reset."""
    if await has_runtime_config():
        return False
    cfg = get_forge_config()
    return len(cfg.repos) > 0


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

