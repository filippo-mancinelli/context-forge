"""Keycloak/OIDC bearer-token validation via PyJWT + JWKS."""
import threading
from typing import Optional

import jwt
from jwt import PyJWKClient

from ..config import get_settings


class OIDCError(Exception):
    pass


_jwks_client: Optional[PyJWKClient] = None
_jwks_url: str = ""
_jwks_lock = threading.Lock()


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client, _jwks_url
    settings = get_settings()
    url = settings.oidc_jwks_url or (
        settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/certs"
    )
    with _jwks_lock:  # guard the check-and-create against concurrent threads
        if _jwks_client is None or url != _jwks_url:
            _jwks_client = PyJWKClient(url, cache_keys=True, lifespan=300)
            _jwks_url = url
        return _jwks_client


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and token.split(".")[0].startswith("eyJ")


def validate_oidc_token(token: str) -> dict:
    settings = get_settings()
    if not settings.oidc_enabled or not settings.oidc_issuer:
        raise OIDCError("OIDC authentication is not enabled")
    options = {"require": ["exp", "iss", "sub"]}
    kwargs = {"algorithms": ["RS256"], "issuer": settings.oidc_issuer, "options": options}
    if settings.oidc_audience:
        kwargs["audience"] = settings.oidc_audience
    else:
        options["verify_aud"] = False
    try:
        key = _get_jwks_client().get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, **kwargs)
    except jwt.PyJWTError as exc:
        raise OIDCError(f"invalid OIDC token: {exc}") from exc
    except Exception as exc:
        raise OIDCError(f"OIDC validation failed: {exc}") from exc
