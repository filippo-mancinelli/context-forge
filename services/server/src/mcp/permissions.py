"""MCP tool authorization: Keycloak groups -> permissions, enforced per tool."""
import functools
from contextvars import ContextVar
from typing import Iterable, Optional

try:
    from fastmcp.exceptions import ToolError
except ImportError:  # older fastmcp
    ToolError = PermissionError

PERMISSIONS = (
    "context-read",
    "context-write",
    "db-query",
    "db-write",
    "repo-write",
    "jobs",
    "ssh-read",
    "ssh-write",
    "projects-write",
    "sources-write",
)

ORG_ROLES = ("viewer", "member", "admin", "owner")

# Default "scala prudente" applicati quando l'organizzazione non ha
# personalizzato la matrice in org_role_permissions. Le capability di
# scrittura (db-write, ssh-write, repo-write) sono del solo owner.
DEFAULT_ROLE_PERMISSIONS = {
    "viewer": frozenset({"context-read"}),
    "member": frozenset({"context-read", "context-write", "db-query", "ssh-read"}),
    "admin": frozenset(
        {
            "context-read",
            "context-write",
            "db-query",
            "jobs",
            "ssh-read",
            "projects-write",
            "sources-write",
        }
    ),
    "owner": frozenset({"*"}),
}


def permissions_from_scope(scope: Optional[str]) -> frozenset:
    """Map a legacy API-key scope (CSV of read|write|admin) to MCP permissions."""
    parts = {p.strip() for p in (scope or "").split(",") if p.strip()}
    if "admin" in parts:
        return frozenset({"*"})
    if "write" in parts:
        return frozenset({"context-read", "context-write"})
    if "read" in parts:
        return frozenset({"context-read"})
    return frozenset()


def parse_permissions_csv(value: Optional[str]) -> Optional[frozenset]:
    """Parse the mcp_api_keys.permissions column. None means the column is NULL."""
    if value is None:
        return None
    parts = {p.strip() for p in value.split(",") if p.strip()}
    if "*" in parts:
        return frozenset({"*"})
    return frozenset(p for p in parts if p in PERMISSIONS)


_current_permissions: ContextVar[Optional[frozenset]] = ContextVar("cf_permissions", default=None)


def set_current_permissions(perms: Optional[frozenset]) -> None:
    _current_permissions.set(perms)


def get_current_permissions() -> Optional[frozenset]:
    return _current_permissions.get()


def permissions_from_groups(groups: Optional[Iterable[str]], prefix: str = "/mcp-tools/") -> frozenset:
    perms = set()
    for group in groups or []:
        if not group.startswith(prefix):
            continue
        name = group[len(prefix):].strip("/")
        if name == "admin":
            return frozenset({"*"})
        if name in PERMISSIONS:
            perms.add(name)
    return frozenset(perms)


def requires_permission(permission: str):
    """None (auth off / legacy caller) allows everything; otherwise the set must contain '*' or the permission."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            perms = _current_permissions.get()
            if perms is not None and "*" not in perms and permission not in perms:
                raise ToolError(
                    f"Access denied: tool requires permission '{permission}'. "
                    f"Ask an organization admin to grant it in the dashboard's "
                    f"Organization -> MCP permissions matrix."
                )
            return await fn(*args, **kwargs)
        return wrapper
    return decorator
