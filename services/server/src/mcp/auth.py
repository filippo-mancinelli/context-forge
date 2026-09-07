"""MCP authentication middleware."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..api.oidc import OIDCError, looks_like_jwt, validate_oidc_token
from ..api.security import find_or_create_oidc_user, validate_mcp_api_key
from ..config import get_settings
from ..tenancy import (
    ensure_default_org,
    get_membership_role,
    get_namespace_for_org,
    get_organization_by_slug,
    list_organizations_for_user,
    resolve_role_permissions,
)
from .context import (
    get_current_org_id,
    get_current_project_id,
    set_current_allowed_projects,
    set_current_namespace,
    set_current_org_id,
    set_current_project_id,
    set_current_user_id,
)
from .permissions import parse_permissions_csv, permissions_from_scope, set_current_permissions

logger = logging.getLogger(__name__)


def parse_project_path(path: str) -> Optional[tuple[str, str, str]]:
    """Scompone '/mcp/{org}/{project}[/resto]' in (org_slug, project_slug, path
    da inoltrare a FastMCP). None se il path non è in forma progetto.
    I path riservati (/health, /oauth/, /mcp/oauth/) vanno esclusi PRIMA di
    chiamare questa funzione (skip_paths nel middleware).
    """
    parts = path.split("/")
    # ['', 'mcp', org, project, *resto]
    if len(parts) < 4 or parts[1] != "mcp":
        return None
    org_slug, project_slug = parts[2], parts[3]
    if not org_slug or not project_slug:
        return None
    rest = "/".join(parts[4:])
    forwarded = "/mcp" + (f"/{rest}" if rest else "")
    return org_slug, project_slug, forwarded


def parse_org_only_path(path: str) -> Optional[str]:
    """Riconosce l'endpoint org-level '/mcp/{org}' (senza progetto nel path).

    Ritorna lo slug org, o None se il path non è nella forma org-level. Il
    progetto viene scelto in sessione via il tool ``use_project``.
    """
    parts = path.rstrip("/").split("/")
    # Esattamente ['', 'mcp', org]: 4+ segmenti sono routing per progetto.
    if len(parts) != 3 or parts[1] != "mcp" or not parts[2]:
        return None
    return parts[2]


async def _resolve_org(org_id, user_id=None):
    """Resolve (org_id, memory namespace) for an authenticated MCP request."""
    if org_id is not None:
        return org_id, await get_namespace_for_org(org_id)
    if user_id is not None:
        orgs = await list_organizations_for_user(user_id)
        if orgs:
            return orgs[0]["id"], orgs[0]["memory_namespace"]
    return None, None


async def _resolve_org_from_claims(claims: dict) -> tuple[int, str]:
    slug = claims.get(get_settings().oidc_org_claim)
    if slug:
        org = await get_organization_by_slug(str(slug))
        if org:
            return org["id"], org["memory_namespace"]
    org_id = await ensure_default_org()
    return org_id, await get_namespace_for_org(org_id)


def _www_authenticate_header(request: Request) -> dict[str, str]:
    """Advertise the OAuth protected-resource metadata on 401 responses."""
    settings = get_settings()
    base = settings.public_mcp_url.rstrip("/") or str(request.base_url).rstrip("/")
    return {
        "WWW-Authenticate": (
            f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'
        )
    }


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to validate MCP API keys, OAuth tokens, or OIDC JWTs on incoming requests."""

    def __init__(self, app, auth_mode: str = "enabled"):
        super().__init__(app)
        self.auth_mode = auth_mode

    async def dispatch(self, request: Request, call_next: Callable) -> JSONResponse:
        # Skip auth for health checks, OPTIONS, and OAuth endpoints
        skip_paths = ["/health", "/oauth/", "/mcp/oauth/", "/.well-known/"]
        if request.method == "OPTIONS" or any(
            request.url.path.startswith(path) for path in skip_paths
        ):
            return await call_next(request)

        # Routing per progetto: /mcp/{org}/{project} -> context + path /mcp
        request.state.mcp_org_level = False
        routed = parse_project_path(request.url.path)
        org_only = None if routed is not None else parse_org_only_path(request.url.path)
        if routed is not None:
            org_slug, project_slug, forwarded = routed
            from ..projects import resolve_project_by_slugs

            project = await resolve_project_by_slugs(org_slug, project_slug)
            if project is None:
                return JSONResponse(
                    {"detail": "Unknown organization or project"}, status_code=404
                )
            set_current_org_id(project["org_id"])
            set_current_project_id(project["id"])
            set_current_namespace(project["memory_namespace"])
            request.scope["path"] = forwarded
        elif org_only is not None:
            # Endpoint org-level: risolvo l'org; il progetto viene ripristinato
            # dalla selezione di sessione dopo l'autenticazione (finalize).
            org = await get_organization_by_slug(org_only)
            if org is None:
                return JSONResponse(
                    {"detail": "Unknown organization"}, status_code=404
                )
            set_current_org_id(org["id"])
            set_current_namespace(org["memory_namespace"])
            set_current_project_id(None)
            request.scope["path"] = "/mcp"
            request.state.mcp_org_level = True
        elif request.url.path.rstrip("/").startswith("/mcp"):
            if self.auth_mode != "disabled":
                return JSONResponse(
                    {
                        "detail": "Project endpoint required: use /mcp/{org}/{project}"
                    },
                    status_code=404,
                )
            # Modalità disabled (dev locale): il context resta vuoto e i
            # resolver a valle ricadono su org/progetto default.

        # Check if auth is enabled
        if self.auth_mode == "disabled":
            return await call_next(request)

        settings = get_settings()

        # Try OAuth Bearer token first
        bearer_api_key: str | None = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
            if token and not looks_like_jwt(token):
                # Clients that can only send a bearer header (plain HTTP agents,
                # some MCP clients) present the API key this way.
                bearer_api_key = token

            if settings.oidc_enabled and looks_like_jwt(token):
                try:
                    claims = await asyncio.to_thread(validate_oidc_token, token)
                except OIDCError as exc:
                    logger.warning(f"MCP request with invalid OIDC token: {exc}")
                    return JSONResponse(
                        {"detail": "invalid or expired token"},
                        status_code=401,
                        headers=_www_authenticate_header(request),
                    )

                org_id = get_current_org_id()
                if org_id is None:
                    org_id, namespace = await _resolve_org_from_claims(claims)
                    set_current_org_id(org_id)
                    set_current_namespace(namespace)
                # Find-or-create the user only; never auto-enroll into org membership here
                # (that would make "no membership -> no permissions" unreachable — see C1).
                user_id = await find_or_create_oidc_user(claims)
                set_current_user_id(user_id)
                role = await get_membership_role(org_id, user_id) if org_id else None
                if role is None and (
                    get_current_project_id() is not None
                    or request.state.mcp_org_level
                ):
                    # Endpoint di progetto o org-level: l'org è quella del path;
                    # un utente autenticato ma senza membership viene rifiutato.
                    logger.warning(
                        "MCP OIDC user without membership in path org (sub=%s)",
                        claims.get("sub"),
                    )
                    return JSONResponse(
                        {"detail": "Not a member of this organization"}, status_code=403
                    )

                # Sul path di progetto la membership org non basta: serve
                # l'accesso al progetto (org admin/owner, o project_members).
                path_project_id = get_current_project_id()
                if path_project_id is not None:
                    from ..projects import resolve_project_access

                    if await resolve_project_access(org_id, user_id, path_project_id) is None:
                        logger.warning(
                            "MCP OIDC user without access to path project (sub=%s, project=%s)",
                            claims.get("sub"),
                            path_project_id,
                        )
                        return JSONResponse(
                            {"detail": "Not a member of this project"}, status_code=403
                        )

                set_current_permissions(await resolve_role_permissions(org_id, role))
                request.state.mcp_auth_info = {
                    "auth": "oidc",
                    "sub": claims.get("sub"),
                    "username": claims.get("preferred_username"),
                    "org_id": org_id,
                }
                return await call_next(request)

        # Fall back to the X-API-Key header, or to a bearer that is not a JWT
        api_key = request.headers.get("X-API-Key") or bearer_api_key
        if api_key:
            key_info = await validate_mcp_api_key(api_key)
            if key_info:
                path_project_id = get_current_project_id()
                key_project_id = key_info.get("project_id")
                # Progetti consentiti dalla key: elenco esplicito (tabella ponte),
                # con fallback al singolo project_id legacy. Nessuno dei due =
                # key org-wide (tutti i progetti dell'org).
                allowed = key_info.get("allowed_projects")
                if not allowed and key_project_id is not None:
                    allowed = [key_project_id]
                allowed_set = frozenset(allowed) if allowed else None
                if (
                    path_project_id is not None
                    and allowed_set is not None
                    and path_project_id not in allowed_set
                ):
                    logger.warning(
                        "MCP API key not valid for path project (key=%s)", key_info.get("id")
                    )
                    return JSONResponse(
                        {"detail": "API key is not valid for this project"},
                        status_code=403,
                    )

                path_org_id = get_current_org_id()
                key_org_id = key_info.get("org_id")
                if (
                    path_org_id is not None
                    and key_org_id is not None
                    and key_org_id != path_org_id
                ):
                    logger.warning(
                        "MCP API key bound to another organization (key=%s)", key_info.get("id")
                    )
                    return JSONResponse(
                        {"detail": "API key is not valid for this organization"},
                        status_code=403,
                    )

                # Valid API key
                request.state.mcp_auth_info = {"type": "api_key", **key_info}

                if get_current_org_id() is None:
                    oid, ns = await _resolve_org(key_info.get("org_id"))
                    set_current_org_id(oid)
                    set_current_namespace(ns)
                set_current_allowed_projects(allowed_set)
                key_perms = parse_permissions_csv(key_info.get("permissions"))
                if key_perms is None:
                    # Difesa in profondità per key non ancora backfillate.
                    key_perms = permissions_from_scope(key_info.get("scope"))
                set_current_permissions(key_perms)
                return await call_next(request)
            else:
                host = request.client.host if request.client else "unknown"
                logger.warning(f"MCP request with invalid API key from {host}")
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or expired API key"},
                    headers=_www_authenticate_header(request),
                )

        # No valid auth found
        host = request.client.host if request.client else "unknown"
        if self.auth_mode == "transition":
            logger.warning(
                f"MCP request missing authentication from {host} (transition mode: read-only)"
            )
            # Senza identità niente allow-all: solo lettura del contesto.
            set_current_permissions(frozenset({"context-read"}))
            return await call_next(request)

        logger.warning(f"MCP request missing authentication from {host}")
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing authentication. Provide either X-API-Key header or Authorization: Bearer token."},
            headers=_www_authenticate_header(request),
        )

def add_auth_middleware(app, auth_mode: str = "enabled"):
    """Installa il middleware MCP (routing per progetto + autenticazione).

    Il middleware è sempre presente perché il routing /mcp/{org}/{project}
    serve anche con auth disabilitata; auth_mode governa solo la parte di
    autenticazione ("disabled", "enabled", "transition").
    """
    middleware = MCPAuthMiddleware(app, auth_mode=auth_mode)
    if auth_mode == "disabled":
        logger.info("MCP authentication is DISABLED - all requests allowed")
    else:
        logger.info(f"MCP authentication is {auth_mode.upper()}")

    async def wrapped_app(scope, receive, send):
        await middleware(scope, receive, send)

    return wrapped_app
