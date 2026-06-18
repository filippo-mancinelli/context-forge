"""Request-scoped tenant context for MCP tool execution.

The MCP auth middleware resolves the calling key/token to an organization and
stores its memory namespace here, so memory tools partition data per tenant
without changing their public signatures.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_current_namespace: ContextVar[Optional[str]] = ContextVar("cf_memory_namespace", default=None)


def set_current_namespace(namespace: Optional[str]) -> None:
    _current_namespace.set(namespace)


def get_current_namespace() -> Optional[str]:
    return _current_namespace.get()
