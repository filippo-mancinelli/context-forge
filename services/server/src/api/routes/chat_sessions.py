"""Saved agent-chat conversations and public share links.

Sessions are scoped to (organization, user): each member keeps a private chat
history. Sharing freezes a snapshot of the conversation under an unguessable
token; the public endpoint serves only that snapshot, so later messages stay
private until the owner re-shares.
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...db import get_pool
from ..deps import ActiveOrg, get_active_org, get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/sessions", tags=["chat"])
# Registered separately and exempt from auth: read-only access by share token.
public_router = APIRouter(prefix="/chat/shared", tags=["chat"])

MAX_SESSIONS_LISTED = 100
MAX_TITLE_LEN = 80
# Serialized turns payload guard so a runaway client can't bloat the table.
MAX_TURNS_BYTES = 4_000_000


class SessionSaveRequest(BaseModel):
    turns: list[dict[str, Any]]
    title: Optional[str] = None


def _title_from(req: SessionSaveRequest) -> str:
    if req.title and req.title.strip():
        return req.title.strip()[:MAX_TITLE_LEN]
    for turn in req.turns:
        if turn.get("role") == "user" and str(turn.get("content", "")).strip():
            return " ".join(str(turn["content"]).split())[:MAX_TITLE_LEN]
    return "New chat"


def _turns_json(req: SessionSaveRequest) -> str:
    payload = json.dumps(req.turns, ensure_ascii=False, default=str)
    if len(payload.encode("utf-8")) > MAX_TURNS_BYTES:
        raise HTTPException(status_code=413, detail="Conversation too large to save.")
    return payload


def _summary(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "turn_count": row["turn_count"],
        "shared": row["share_token"] is not None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("")
async def list_sessions(
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, title, share_token, created_at, updated_at,
               jsonb_array_length(turns) AS turn_count
        FROM chat_sessions
        WHERE org_id = $1 AND user_id = $2
        ORDER BY updated_at DESC
        LIMIT $3
        """,
        org.org_id,
        user_id,
        MAX_SESSIONS_LISTED,
    )
    return {"sessions": [_summary(r) for r in rows]}


@router.post("")
async def create_session(
    req: SessionSaveRequest,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO chat_sessions (org_id, user_id, title, turns)
        VALUES ($1, $2, $3, $4::jsonb)
        RETURNING id, title, share_token, created_at, updated_at,
                  jsonb_array_length(turns) AS turn_count
        """,
        org.org_id,
        user_id,
        _title_from(req),
        _turns_json(req),
    )
    return {"status": "ok", "session": _summary(row)}


@router.get("/{session_id}")
async def get_session(
    session_id: int,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, title, turns, share_token, created_at, updated_at,
               jsonb_array_length(turns) AS turn_count
        FROM chat_sessions
        WHERE id = $1 AND org_id = $2 AND user_id = $3
        """,
        session_id,
        org.org_id,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {**_summary(row), "turns": json.loads(row["turns"])}


@router.put("/{session_id}")
async def update_session(
    session_id: int,
    req: SessionSaveRequest,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE chat_sessions
        SET turns = $1::jsonb, title = COALESCE(NULLIF($2, ''), title), updated_at = NOW()
        WHERE id = $3 AND org_id = $4 AND user_id = $5
        RETURNING id, title, share_token, created_at, updated_at,
                  jsonb_array_length(turns) AS turn_count
        """,
        _turns_json(req),
        (req.title or "").strip()[:MAX_TITLE_LEN],
        session_id,
        org.org_id,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"status": "ok", "session": _summary(row)}


@router.delete("/{session_id}")
async def delete_session(
    session_id: int,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    pool = await get_pool()
    deleted = await pool.execute(
        "DELETE FROM chat_sessions WHERE id = $1 AND org_id = $2 AND user_id = $3",
        session_id,
        org.org_id,
        user_id,
    )
    if deleted == "DELETE 0":
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"status": "ok"}


@router.post("/{session_id}/share")
async def share_session(
    session_id: int,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    """Create (or refresh) the public snapshot of this conversation.

    The token stays stable across re-shares so previously sent links keep
    working; the snapshot is updated to the current turns.
    """
    pool = await get_pool()
    token = secrets.token_urlsafe(24)
    row = await pool.fetchrow(
        """
        UPDATE chat_sessions
        SET share_token = COALESCE(share_token, $1),
            shared_snapshot = turns,
            shared_at = NOW()
        WHERE id = $2 AND org_id = $3 AND user_id = $4
        RETURNING share_token, shared_at
        """,
        token,
        session_id,
        org.org_id,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"status": "ok", "share_token": row["share_token"], "shared_at": row["shared_at"]}


@router.delete("/{session_id}/share")
async def unshare_session(
    session_id: int,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE chat_sessions
        SET share_token = NULL, shared_snapshot = NULL, shared_at = NULL
        WHERE id = $1 AND org_id = $2 AND user_id = $3
        RETURNING id
        """,
        session_id,
        org.org_id,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"status": "ok"}


@public_router.get("/{token}")
async def get_shared_session(token: str):
    """Public, read-only view of a shared conversation snapshot (no auth)."""
    if not token or len(token) > 128:
        raise HTTPException(status_code=404, detail="Shared chat not found")
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT title, shared_snapshot, shared_at
        FROM chat_sessions
        WHERE share_token = $1 AND shared_snapshot IS NOT NULL
        """,
        token,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Shared chat not found")
    return {
        "title": row["title"],
        "turns": json.loads(row["shared_snapshot"]),
        "shared_at": row["shared_at"],
    }
