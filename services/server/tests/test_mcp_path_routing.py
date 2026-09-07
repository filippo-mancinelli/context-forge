from src.mcp.auth import parse_project_path


def test_parse_basic():
    assert parse_project_path("/mcp/acme/webshop") == ("acme", "webshop", "/mcp")


def test_parse_with_subpath():
    assert parse_project_path("/mcp/acme/webshop/messages") == (
        "acme",
        "webshop",
        "/mcp/messages",
    )


def test_parse_bare_mcp_is_none():
    assert parse_project_path("/mcp") is None
    assert parse_project_path("/mcp/") is None


def test_parse_org_only_is_none():
    assert parse_project_path("/mcp/acme") is None


def test_parse_non_mcp_is_none():
    assert parse_project_path("/health") is None
    assert parse_project_path("/oauth/token") is None
