"""OAuth 2.0 bridge that delegates user authentication to Keycloak.

context-forge acts as a facade Authorization Server: it publishes the OAuth
discovery metadata and implements registration/authorize/callback/token, but
the user is authenticated by Keycloak. The token handed back to the MCP client
is the Keycloak JWT (aud=context-forge), which the MCP middleware already validates
via JWKS.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from ..config import get_settings
from ..db import get_pool
from .server import mcp

logger = logging.getLogger(__name__)

FLOW_TTL_SECONDS = 600
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _public_base(request: Request) -> str:
    """Public base URL of the MCP server used in discovery documents."""
    configured = get_settings().public_mcp_url.rstrip("/")
    return configured or str(request.base_url).rstrip("/")


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def protected_resource_metadata(request: Request) -> Response:
    """Advertise this MCP server as an OAuth protected resource."""
    base = _public_base(request)
    return JSONResponse({"resource": base, "authorization_servers": [base]})


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def authorization_server_metadata(request: Request) -> Response:
    """Publish the facade Authorization Server metadata."""
    base = _public_base(request)
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
    )


def _is_loopback_redirect(uri: str) -> bool:
    """Accept only http loopback redirect URIs for dynamic registration."""
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    return parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS


async def save_dynamic_client(client_id: str, name: str, redirect_uris: list[str]) -> None:
    """Persist a client created via Dynamic Client Registration."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO oauth_clients (id, client_id, name, redirect_uris, scopes, dynamic)
               VALUES ($1, $1, $2, $3, 'read,write', TRUE)""",
            client_id, name, redirect_uris,
        )


@mcp.custom_route("/oauth/register", methods=["POST"])
async def register_client(request: Request) -> Response:
    """RFC 7591 Dynamic Client Registration restricted to loopback redirects."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)

    redirect_uris = body.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse(
            {"error": "invalid_redirect_uri", "error_description": "redirect_uris is required"},
            status_code=400,
        )
    for uri in redirect_uris:
        if not isinstance(uri, str) or not _is_loopback_redirect(uri):
            host = request.client.host if request.client else "unknown"
            logger.warning("Dynamic registration rejected non-loopback redirect from %s", host)
            return JSONResponse(
                {"error": "invalid_redirect_uri",
                 "error_description": "only loopback redirect URIs are allowed"},
                status_code=400,
            )

    client_id = "dcr_" + secrets.token_urlsafe(24)
    client_name = str(body.get("client_name") or "MCP dynamic client")[:200]
    await save_dynamic_client(client_id, client_name, redirect_uris)
    return JSONResponse(
        {
            "client_id": client_id,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        status_code=201,
    )


def _pkce_challenge(verifier: str) -> str:
    """S256 code challenge for a PKCE verifier (base64url, no padding)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _pkce_verify(code_verifier: str, expected_challenge: str) -> bool:
    """True se il code_verifier corrisponde al challenge salvato (S256).
    Un verifier malformato (non-ASCII o non codificabile) è semplicemente non valido."""
    try:
        computed = _pkce_challenge(code_verifier)
    except (UnicodeEncodeError, ValueError):
        return False
    return hmac.compare_digest(computed, expected_challenge or "")


def _redirect_with(uri: str, params: dict) -> RedirectResponse:
    """302 to ``uri`` with the given query params, dropping empty values."""
    clean = {k: v for k, v in params.items() if v}
    return RedirectResponse(f"{uri}?{urlencode(clean)}", status_code=302)


async def load_client(client_id: str) -> Optional[dict]:
    """Load a registered OAuth client by its client_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM oauth_clients WHERE client_id = $1", client_id)
    return dict(row) if row else None


async def create_flow(
    bridge_state: str,
    client_id: str,
    client_redirect_uri: str,
    client_state: str,
    client_code_challenge: str,
    kc_pkce_verifier: str,
) -> None:
    """Persist the correlation state for one bridged authorization."""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=FLOW_TTL_SECONDS)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO oauth_bridge_flows
                   (bridge_state, client_id, client_redirect_uri, client_state,
                    client_code_challenge, kc_pkce_verifier, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            bridge_state, client_id, client_redirect_uri, client_state,
            client_code_challenge, kc_pkce_verifier, expires_at,
        )


@mcp.custom_route("/oauth/authorize", methods=["GET"])
async def authorize(request: Request) -> Response:
    """Start the bridged authorization: persist state, redirect to Keycloak."""
    params = request.query_params
    client_id = params.get("client_id")
    redirect_uri = params.get("redirect_uri")
    code_challenge = params.get("code_challenge")
    method = params.get("code_challenge_method")
    response_type = params.get("response_type", "code")
    client_state = params.get("state", "")

    if not client_id:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "client_id is required"},
            status_code=400,
        )
    client = await load_client(client_id)
    if client is None:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    if not redirect_uri or redirect_uri not in (client.get("redirect_uris") or []):
        return JSONResponse(
            {"error": "invalid_request", "error_description": "unknown redirect_uri"},
            status_code=400,
        )
    if response_type != "code":
        return _redirect_with(redirect_uri, {"error": "unsupported_response_type", "state": client_state})
    if not code_challenge or method != "S256":
        return _redirect_with(redirect_uri, {"error": "invalid_request", "state": client_state})

    settings = get_settings()
    bridge_state = secrets.token_urlsafe(32)
    kc_verifier = secrets.token_urlsafe(32)
    await create_flow(bridge_state, client_id, redirect_uri, client_state, code_challenge, kc_verifier)

    kc_params = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "redirect_uri": f"{_public_base(request)}/oauth/callback",
        "scope": "openid",
        "state": bridge_state,
        "code_challenge": _pkce_challenge(kc_verifier),
        "code_challenge_method": "S256",
    }
    kc_url = settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/auth?" + urlencode(kc_params)
    return RedirectResponse(kc_url, status_code=302)


async def load_flow_by_state(bridge_state: str) -> Optional[dict]:
    """Load a non-expired bridge flow by its bridge_state."""
    now = datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM oauth_bridge_flows WHERE bridge_state = $1 AND expires_at > $2",
            bridge_state, now,
        )
    return dict(row) if row else None


async def attach_bridge_code(
    bridge_state: str, bridge_code: str, kc_access_token: str, kc_refresh_token: Optional[str]
) -> None:
    """Attach the minted bridge code and Keycloak tokens to a flow."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE oauth_bridge_flows
                   SET bridge_code = $2, kc_access_token = $3, kc_refresh_token = $4
                 WHERE bridge_state = $1""",
            bridge_state, bridge_code, kc_access_token, kc_refresh_token,
        )


async def _keycloak_token(data: dict) -> dict:
    """POST to the Keycloak token endpoint and return the parsed JSON."""
    settings = get_settings()
    url = settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, data=data)
    resp.raise_for_status()
    return resp.json()


async def exchange_keycloak_code(code: str, redirect_uri: str, code_verifier: str) -> dict:
    """Exchange a Keycloak authorization code for tokens (confidential client + PKCE)."""
    settings = get_settings()
    return await _keycloak_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
            "code_verifier": code_verifier,
        }
    )


@mcp.custom_route("/oauth/callback", methods=["GET"])
async def callback(request: Request) -> Response:
    """Receive the Keycloak code, exchange it for a JWT, mint a one-time bridge code."""
    params = request.query_params
    error = params.get("error")
    if error:
        return JSONResponse(
            {"error": error, "error_description": params.get("error_description", "")},
            status_code=400,
        )
    kc_code = params.get("code")
    bridge_state = params.get("state", "")
    if not kc_code or not bridge_state:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    flow = await load_flow_by_state(bridge_state)
    if flow is None:
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "unknown or expired state"},
            status_code=400,
        )
    try:
        tokens = await exchange_keycloak_code(
            kc_code, f"{_public_base(request)}/oauth/callback", flow["kc_pkce_verifier"]
        )
    except httpx.HTTPStatusError as exc:
        # Keycloak rejected the grant (reused/expired code, redirect_uri mismatch): definitive.
        logger.warning("Keycloak rejected token exchange: %s", exc)
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "code rejected by identity provider"},
            status_code=400,
        )
    except httpx.HTTPError as exc:
        # Network/timeout talking to the IdP: transient.
        logger.warning("Keycloak token exchange failed: %s", exc)
        return JSONResponse(
            {"error": "temporarily_unavailable", "error_description": "identity provider unreachable"},
            status_code=503,
        )

    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "no token issued"}, status_code=400
        )
    access_token = tokens["access_token"]
    bridge_code = secrets.token_urlsafe(32)
    await attach_bridge_code(bridge_state, bridge_code, access_token, tokens.get("refresh_token"))
    return _redirect_with(flow["client_redirect_uri"], {"code": bridge_code, "state": flow["client_state"]})


def _access_token_ttl(access_token: str) -> int:
    """Seconds until the Keycloak JWT expires (best-effort, no signature check)."""
    try:
        claims = jwt.decode(access_token, options={"verify_signature": False})
        remaining = int(claims.get("exp", 0)) - int(datetime.now(timezone.utc).timestamp())
        return max(0, remaining)
    except Exception:
        return 3600


async def consume_flow_by_code(bridge_code: str) -> Optional[dict]:
    """Load and delete (one-time) a bridge flow by its bridge code."""
    now = datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM oauth_bridge_flows WHERE bridge_code = $1 AND expires_at > $2 FOR UPDATE",
                bridge_code, now,
            )
            if row is None:
                return None
            await conn.execute(
                "DELETE FROM oauth_bridge_flows WHERE bridge_state = $1", row["bridge_state"]
            )
    return dict(row)


async def purge_expired_flows() -> int:
    """Elimina i flow OAuth scaduti (contengono token in chiaro): niente ritenzione oltre il TTL."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM oauth_bridge_flows WHERE expires_at < NOW()")
    # result è tipo "DELETE N"
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def refresh_keycloak_token(refresh_token: str) -> dict:
    """Refresh Keycloak tokens on behalf of the client."""
    settings = get_settings()
    return await _keycloak_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
        }
    )


def _token_response(access_token: str, refresh_token: Optional[str]) -> JSONResponse:
    body = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": _access_token_ttl(access_token),
    }
    if refresh_token:
        body["refresh_token"] = refresh_token
    return JSONResponse(body)


async def _grant_authorization_code(form) -> Response:
    code = form.get("code")
    code_verifier = form.get("code_verifier")
    client_id = form.get("client_id")
    if not code or not code_verifier:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    flow = await consume_flow_by_code(code)
    if flow is None:
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "unknown or expired code"},
            status_code=400,
        )
    if client_id is not None and client_id != flow["client_id"]:
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "client mismatch"}, status_code=400
        )
    if not code_verifier or not _pkce_verify(code_verifier, flow["client_code_challenge"]):
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "PKCE verification failed"},
            status_code=400,
        )
    return _token_response(flow["kc_access_token"], flow["kc_refresh_token"])


async def _grant_refresh_token(form) -> Response:
    refresh_token = form.get("refresh_token")
    if not refresh_token:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    try:
        tokens = await refresh_keycloak_token(refresh_token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 401):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
    except httpx.HTTPError:
        return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)

    access_token = tokens.get("access_token")
    if not access_token:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    return _token_response(access_token, tokens.get("refresh_token") or refresh_token)


@mcp.custom_route("/oauth/token", methods=["POST"])
async def token(request: Request) -> Response:
    """Exchange a bridge authorization code or a refresh token for a Keycloak JWT."""
    form = await request.form()
    grant_type = form.get("grant_type")
    if grant_type == "authorization_code":
        return await _grant_authorization_code(form)
    if grant_type == "refresh_token":
        return await _grant_refresh_token(form)
    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
