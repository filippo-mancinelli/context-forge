from src.indexer.git_manager import _inject_token


def test_inject_token_upgrades_http_to_https():
    # Un host che redirige http->https perde le credenziali sul redirect: il
    # token va sempre iniettato su https.
    url = _inject_token("http://git.example.com/group/repo", "TOK")
    assert url == "https://oauth2:TOK@git.example.com/group/repo"


def test_inject_token_keeps_https():
    url = _inject_token("https://git.example.com/group/repo", "TOK")
    assert url == "https://oauth2:TOK@git.example.com/group/repo"


def test_inject_token_preserves_port():
    url = _inject_token("http://git.example.com:8443/g/r", "TOK")
    assert url == "https://oauth2:TOK@git.example.com:8443/g/r"
