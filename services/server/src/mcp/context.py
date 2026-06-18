"""Request-scoped tenant context for MCP tool execution.

The MCP auth middleware resolves the calling key/token to an organization and
stores it here, so tools partition data per tenant without changing their
public signatures. When unset (e.g. single-tenant deployments with auth
disabled) tools fall back to the default organization.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_current_namespace: ContextVar[Optional[str]] = ContextVar("cf_memory_namespace", default=None)
_current_org_id: ContextVar[Optional[int]] = ContextVar("cf_org_id", default=None)


def set_current_namespace(namespace: Optional[str]) -> None:
    _current_namespace.set(namespace)


def get_current_namespace() -> Optional[str]:
    return _current_namespace.get()


def set_current_org_id(org_id: Optional[int]) -> None:
    _current_org_id.set(org_id)


def get_current_org_id() -> Optional[int]:
    return _current_org_id.get()


async def resolve_org_id() -> Optional[int]:
    """Return the context org id, falling back to the default organization."""
    org_id = _current_org_id.get()
    if org_id is not None:
        return org_id
    from ..tenancy import ensure_default_org

    return await ensure_default_org()
