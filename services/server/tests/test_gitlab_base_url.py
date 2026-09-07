from src import config
from src.api.routes.gitlab import _gitlab_base_url


def _set_gitlab_url(monkeypatch, value):
    monkeypatch.setattr(config.get_settings(), "gitlab_url", value, raising=False)


def test_gitlab_url_setting_defaults_to_empty():
    assert config.Settings(_env_file=None).gitlab_url == ""


def test_base_url_falls_back_to_gitlab_com(monkeypatch):
    _set_gitlab_url(monkeypatch, "")
    assert _gitlab_base_url() == "https://gitlab.com/api/v4"


def test_base_url_appends_api_path_to_instance_root(monkeypatch):
    _set_gitlab_url(monkeypatch, "https://gitlab.example.com")
    assert _gitlab_base_url() == "https://gitlab.example.com/api/v4"


def test_base_url_strips_whitespace_and_trailing_slash(monkeypatch):
    _set_gitlab_url(monkeypatch, "  https://gitlab.example.com/  ")
    assert _gitlab_base_url() == "https://gitlab.example.com/api/v4"


def test_base_url_keeps_explicit_api_v4_url(monkeypatch):
    _set_gitlab_url(monkeypatch, "https://gitlab.example.com/api/v4")
    assert _gitlab_base_url() == "https://gitlab.example.com/api/v4"
