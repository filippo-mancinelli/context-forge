import asyncio

import pytest

from src.mcp import context as mcp_context
from src.mcp import permissions as perms


def _underlying(tool):
    return getattr(tool, "fn", tool)


def test_member_without_permission_is_rejected():
    from src.mcp import project_admin
    perms.set_current_permissions(frozenset({"context-read", "db-query"}))
    try:
        with pytest.raises(Exception, match="projects-write"):
            asyncio.run(_underlying(project_admin.create_project)("New project"))
    finally:
        perms.set_current_permissions(None)


def test_api_key_identity_is_rejected_even_with_the_permission():
    """Una API key con projects-write non deve poter creare contenitori nuovi:
    è legata a progetti esistenti, non è un'identità di governance."""
    from src.mcp import project_admin
    perms.set_current_permissions(frozenset({"projects-write"}))
    mcp_context.set_current_org_id(1)
    mcp_context.set_current_user_id(None)
    try:
        result = asyncio.run(_underlying(project_admin.create_project)("New project"))
        assert result["status"] == "error"
        assert "user authentication" in result["error"]
    finally:
        perms.set_current_permissions(None)
        mcp_context.set_current_org_id(None)
