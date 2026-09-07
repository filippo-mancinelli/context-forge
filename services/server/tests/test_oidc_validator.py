import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src import config
from src.api import oidc

ISSUER = "https://sso.lascaux.it/realms/askmesuite"


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc_env(monkeypatch, rsa_key):
    settings = config.get_settings()
    monkeypatch.setattr(settings, "oidc_enabled", True, raising=False)
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER, raising=False)
    monkeypatch.setattr(settings, "oidc_audience", "", raising=False)
    fake = SimpleNamespace(get_signing_key_from_jwt=lambda tok: SimpleNamespace(key=rsa_key.public_key()))
    monkeypatch.setattr(oidc, "_get_jwks_client", lambda: fake)
    return rsa_key


def _sign(key, **overrides):
    claims = {"iss": ISSUER, "sub": "user-1", "exp": int(time.time()) + 300,
              "preferred_username": "mario.rossi", "groups": ["/mcp-tools/context-read"]}
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


def test_valid_token_returns_claims(oidc_env):
    claims = oidc.validate_oidc_token(_sign(oidc_env))
    assert claims["preferred_username"] == "mario.rossi"
    assert claims["groups"] == ["/mcp-tools/context-read"]


def test_wrong_issuer_rejected(oidc_env):
    with pytest.raises(oidc.OIDCError):
        oidc.validate_oidc_token(_sign(oidc_env, iss="https://evil.example/realms/x"))


def test_expired_token_rejected(oidc_env):
    with pytest.raises(oidc.OIDCError):
        oidc.validate_oidc_token(_sign(oidc_env, exp=int(time.time()) - 10))


def test_disabled_oidc_rejects(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "oidc_enabled", False, raising=False)
    with pytest.raises(oidc.OIDCError):
        oidc.validate_oidc_token("x.y.z")


def test_looks_like_jwt():
    assert oidc.looks_like_jwt("eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.sig")
    assert not oidc.looks_like_jwt("cf_opaque_session_token")
