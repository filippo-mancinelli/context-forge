"""Tests for RP-initiated Keycloak logout via end-session URL."""
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlparse


def test_logout_endpoint_constructs_logout_url_on_oidc_token(monkeypatch):
    """POST /auth/logout should construct logout_url when session has id_token."""
    from fastapi.testclient import TestClient
    from src import config
    from src.api.app import api

    # Patch settings.
    settings = config.get_settings()
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_issuer", "https://sso.lascaux.it/realms/askmesuite")
    monkeypatch.setattr(settings, "ui_base_url", "https://forge.lascaux.it")

    # Mock delete_session to return an id_token.
    id_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.body.sig"

    async def mock_delete_session(token):
        return id_token

    monkeypatch.setattr("src.api.routes.auth.delete_session", mock_delete_session)

    # Mock require_valid_token_or_raise to allow the request.
    async def mock_require_valid(x):
        pass
    monkeypatch.setattr("src.api.routes.auth.require_valid_token_or_raise", mock_require_valid)

    client = TestClient(api)
    response = client.post("/api/auth/logout", headers={"Authorization": "Bearer token123"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "logout_url" in data

    # Verify logout_url structure.
    logout_url = data["logout_url"]
    parsed = urlparse(logout_url)
    assert parsed.scheme == "https"
    assert "sso.lascaux.it" in parsed.netloc
    assert "/protocol/openid-connect/logout" in parsed.path

    qs = parse_qs(parsed.query)
    assert qs["id_token_hint"][0] == id_token
    assert qs["post_logout_redirect_uri"][0] == "https://forge.lascaux.it"


def test_logout_endpoint_omits_logout_url_on_password_login(monkeypatch):
    """POST /auth/logout should omit logout_url when session has no id_token."""
    from fastapi.testclient import TestClient
    from src import config
    from src.api.app import api

    # Patch settings.
    settings = config.get_settings()
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_issuer", "https://sso.lascaux.it/realms/askmesuite")
    monkeypatch.setattr(settings, "ui_base_url", "https://forge.lascaux.it")

    # Mock delete_session to return None (password login).
    async def mock_delete_session(token):
        return None

    monkeypatch.setattr("src.api.routes.auth.delete_session", mock_delete_session)

    # Mock require_valid_token_or_raise to allow the request.
    async def mock_require_valid(x):
        pass
    monkeypatch.setattr("src.api.routes.auth.require_valid_token_or_raise", mock_require_valid)

    client = TestClient(api)
    response = client.post("/api/auth/logout", headers={"Authorization": "Bearer token123"})

    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "ok"}
    assert "logout_url" not in data


def test_logout_endpoint_omits_logout_url_when_oidc_disabled(monkeypatch):
    """POST /auth/logout should omit logout_url when OIDC is disabled."""
    from fastapi.testclient import TestClient
    from src import config
    from src.api.app import api

    # Patch settings: OIDC disabled.
    settings = config.get_settings()
    monkeypatch.setattr(settings, "oidc_enabled", False)

    # Mock delete_session to return an id_token.
    id_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.body.sig"

    async def mock_delete_session(token):
        return id_token

    monkeypatch.setattr("src.api.routes.auth.delete_session", mock_delete_session)

    # Mock require_valid_token_or_raise to allow the request.
    async def mock_require_valid(x):
        pass
    monkeypatch.setattr("src.api.routes.auth.require_valid_token_or_raise", mock_require_valid)

    client = TestClient(api)
    response = client.post("/api/auth/logout", headers={"Authorization": "Bearer token123"})

    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "ok"}
    assert "logout_url" not in data


def test_oidc_callback_passes_id_token_to_create_session(monkeypatch):
    """oidc_callback should extract and pass id_token from token response to create_session."""
    import asyncio
    from unittest.mock import call
    from src.api.routes.auth import oidc_callback
    from src import config

    async def _run():
        # Setup mocks for the OIDC callback.
        settings = config.get_settings()
        monkeypatch.setattr(settings, "oidc_enabled", True)
        monkeypatch.setattr(settings, "oidc_issuer", "https://sso.lascaux.it/realms/askmesuite")
        monkeypatch.setattr(settings, "oidc_client_id", "test-client")
        monkeypatch.setattr(settings, "oidc_client_secret", "test-secret")
        monkeypatch.setattr(settings, "public_base_url", "https://forge.lascaux.it")
        monkeypatch.setattr(settings, "ui_base_url", "https://forge.lascaux.it")

        # Mock the state.
        from src.api.routes import auth as auth_route
        state = auth_route._issue_state()

        # Mock httpx.AsyncClient response.
        id_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.body.sig"
        access_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.access.sig"

        class FakeResp:
            status_code = 200
            def json(self):
                return {
                    "access_token": access_token,
                    "id_token": id_token,
                }

        class FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return False
            async def post(self, url, data=None):
                return FakeResp()

        # Mock validate_oidc_token.
        monkeypatch.setattr(
            "src.api.routes.auth.validate_oidc_token",
            lambda x: {"sub": "user123", "email": "test@example.com"},
        )

        # Mock provision_oidc_user.
        async def mock_provision_oidc_user(claims):
            return 1

        monkeypatch.setattr("src.api.routes.auth.provision_oidc_user", mock_provision_oidc_user)

        # Track what create_session is called with.
        create_session_calls = []

        async def mock_create_session(user_id, id_token=None):
            create_session_calls.append({"user_id": user_id, "id_token": id_token})
            return "session_token"

        monkeypatch.setattr("src.api.routes.auth.create_session", mock_create_session)

        with patch("src.api.routes.auth.httpx.AsyncClient", FakeClient):
            response = await oidc_callback(code="auth_code", state=state)

            # Verify create_session was called with id_token.
            assert len(create_session_calls) == 1
            assert create_session_calls[0]["user_id"] == 1
            assert create_session_calls[0]["id_token"] == id_token

    asyncio.run(_run())


class _FakeConn:
    """Minimal asyncpg-connection stand-in that records execute() calls and
    returns a scripted value from fetchval()."""

    def __init__(self, fetchval_return=None):
        self.fetchval_return = fetchval_return
        self.executed = []

    async def execute(self, query, *args):
        self.executed.append((query, args))

    async def fetchval(self, query, *args):
        self.executed.append((query, args))
        return self.fetchval_return


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _FakeAcquire(self.conn)


def test_create_session_encrypts_id_token_at_rest(monkeypatch):
    """create_session must store the OIDC id_token encrypted, not as plaintext."""
    import asyncio
    from src import config
    from src.api import security
    from src.datasources.secrets import decrypt_secret

    settings = config.get_settings()
    monkeypatch.setattr(settings, "encryption_key", "test-encryption-key-for-logout")

    id_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.body.sig"
    conn = _FakeConn()

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr(security, "get_pool", fake_get_pool)

    asyncio.run(security.create_session(1, id_token=id_token))

    assert len(conn.executed) == 1
    _, args = conn.executed[0]
    stored_id_token = args[3]

    # It must not be the raw token, and it must round-trip via decrypt_secret.
    assert stored_id_token != id_token
    assert stored_id_token.startswith("enc:")
    assert decrypt_secret(stored_id_token) == id_token


def test_create_session_without_id_token_stores_none(monkeypatch):
    """Password logins (no id_token) must keep storing NULL, not an encrypted empty string."""
    import asyncio
    from src import config
    from src.api import security

    settings = config.get_settings()
    monkeypatch.setattr(settings, "encryption_key", "test-encryption-key-for-logout")

    conn = _FakeConn()

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr(security, "get_pool", fake_get_pool)

    asyncio.run(security.create_session(1))

    _, args = conn.executed[0]
    assert args[3] is None


def test_delete_session_decrypts_id_token(monkeypatch):
    """delete_session must decrypt the stored id_token before returning it."""
    import asyncio
    from src import config
    from src.api import security
    from src.datasources.secrets import encrypt_secret

    settings = config.get_settings()
    monkeypatch.setattr(settings, "encryption_key", "test-encryption-key-for-logout")

    id_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.body.sig"
    stored_id_token = encrypt_secret(id_token)
    assert stored_id_token.startswith("enc:")

    conn = _FakeConn(fetchval_return=stored_id_token)

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr(security, "get_pool", fake_get_pool)

    result = asyncio.run(security.delete_session("some-session-token"))

    assert result == id_token


def test_delete_session_returns_none_when_no_id_token_stored(monkeypatch):
    """delete_session must keep returning None for password logins (no id_token column value)."""
    import asyncio
    from src import config
    from src.api import security

    settings = config.get_settings()
    monkeypatch.setattr(settings, "encryption_key", "test-encryption-key-for-logout")

    conn = _FakeConn(fetchval_return=None)

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr(security, "get_pool", fake_get_pool)

    result = asyncio.run(security.delete_session("some-session-token"))

    assert result is None


def test_delete_session_falls_back_to_none_on_undecryptable_token(monkeypatch):
    """If the stored value can't be decrypted (e.g. ENCRYPTION_KEY was rotated),
    logout must degrade gracefully to None rather than raising/500ing."""
    import asyncio
    from src import config
    from src.api import security
    from src.datasources.secrets import encrypt_secret

    settings = config.get_settings()

    # Encrypt with one key, then rotate the key before decrypting.
    monkeypatch.setattr(settings, "encryption_key", "original-key")
    stored_id_token = encrypt_secret("eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.body.sig")
    monkeypatch.setattr(settings, "encryption_key", "rotated-key")

    conn = _FakeConn(fetchval_return=stored_id_token)

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr(security, "get_pool", fake_get_pool)

    result = asyncio.run(security.delete_session("some-session-token"))

    assert result is None


def test_logout_endpoint_returns_decrypted_id_token_end_to_end(monkeypatch):
    """The logout endpoint must forward the DECRYPTED id_token in logout_url,
    even though it is stored encrypted at rest."""
    from fastapi.testclient import TestClient
    from src import config
    from src.api import security
    from src.api.app import api
    from src.datasources.secrets import encrypt_secret

    settings = config.get_settings()
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_issuer", "https://sso.lascaux.it/realms/askmesuite")
    monkeypatch.setattr(settings, "ui_base_url", "https://forge.lascaux.it")
    monkeypatch.setattr(settings, "encryption_key", "test-encryption-key-for-logout")

    id_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.body.sig"
    stored_id_token = encrypt_secret(id_token)
    assert stored_id_token != id_token

    conn = _FakeConn(fetchval_return=stored_id_token)

    async def fake_get_pool():
        return _FakePool(conn)

    # Patch the DB layer only: delete_session itself runs for real (not mocked),
    # so this exercises the actual encrypt-at-rest / decrypt-on-logout path.
    monkeypatch.setattr(security, "get_pool", fake_get_pool)

    # Mock require_valid_token_or_raise to allow the request.
    async def mock_require_valid(x):
        pass
    monkeypatch.setattr("src.api.routes.auth.require_valid_token_or_raise", mock_require_valid)

    client = TestClient(api)
    response = client.post("/api/auth/logout", headers={"Authorization": "Bearer token123"})

    assert response.status_code == 200
    data = response.json()
    assert "logout_url" in data

    qs = parse_qs(urlparse(data["logout_url"]).query)
    assert qs["id_token_hint"][0] == id_token
