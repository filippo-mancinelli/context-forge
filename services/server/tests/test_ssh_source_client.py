"""Sicurezza del client SSH: confinamento del path e filtri glob.

La connessione SFTP è simulata: si verifica la logica di confinamento e di
filtro, non un accesso reale.
"""
import posixpath
import stat as stat_mod

import pytest

from src.ssh_sources import client


# ── confinamento del path ─────────────────────────────────────────────────────

def test_confine_allows_paths_under_root():
    assert client._confine("/etc/askme", "nginx.conf") == "/etc/askme/nginx.conf"
    assert client._confine("/etc/askme", "sub/app.yml") == "/etc/askme/sub/app.yml"
    assert client._confine("/etc/askme", "") == "/etc/askme"


def test_confine_rejects_parent_escape():
    with pytest.raises(client.SSHSourceError):
        client._confine("/etc/askme", "../secret")
    with pytest.raises(client.SSHSourceError):
        client._confine("/etc/askme", "sub/../../etc/shadow")


def test_confine_rejects_absolute_escape():
    # Un path assoluto viene comunque re-ancorato sotto la radice, non fuori.
    assert client._confine("/etc/askme", "/etc/shadow") == "/etc/askme/etc/shadow"


def test_confine_rejects_sibling_prefix():
    # /etc/askme-other non deve passare per /etc/askme.
    with pytest.raises(client.SSHSourceError):
        client._confine("/etc/askme", "../askme-other/x")


# ── confined_path (guardia pubblica, usata prima di proporre una scrittura) ──

def test_confined_path_returns_the_resolved_path_inside_root():
    conn = {"root_path": "/etc/askme"}
    assert client.confined_path(conn, "sub/app.yml") == "/etc/askme/sub/app.yml"


def test_confined_path_rejects_an_escaping_path():
    conn = {"root_path": "/etc/askme"}
    with pytest.raises(ValueError, match="escapes the source root"):
        client.confined_path(conn, "../../etc/passwd")


# ── filtri glob ───────────────────────────────────────────────────────────────

def test_matches_include_only():
    inc = ["*.conf", "*.yml"]
    assert client._matches("nginx.conf", inc, [])
    assert client._matches("app.yml", inc, [])
    assert not client._matches("app.jar", inc, [])


def test_matches_exclude_wins():
    assert not client._matches("secrets.yml", ["*.yml"], ["secrets*"])


def test_matches_empty_include_accepts_all_but_excluded():
    assert client._matches("anything.txt", [], [])
    assert not client._matches("id_rsa", [], ["id_rsa", "*.key"])


# ── list_files con SFTP simulato ──────────────────────────────────────────────

class _Attr:
    def __init__(self, filename, size=10, is_dir=False, mtime=0):
        self.filename = filename
        self.st_size = size
        self.st_mtime = mtime
        self.st_mode = (stat_mod.S_IFDIR if is_dir else stat_mod.S_IFREG) | 0o644


class _FakeSFTP:
    def __init__(self, tree):
        # tree: {dir_path: [ _Attr, ... ]}
        self.tree = tree

    def listdir_attr(self, path):
        return self.tree.get(path, [])


class _FakeClient:
    def __init__(self, sftp):
        self._sftp = sftp

    def open_sftp(self):
        return self._sftp

    def close(self):
        pass


def test_list_files_filters_and_confines(monkeypatch):
    root = "/etc/askme"
    tree = {
        root: [
            _Attr("nginx.conf"),
            _Attr("app.yml"),
            _Attr("logo.png"),
            _Attr("sub", is_dir=True),
        ],
        posixpath.join(root, "sub"): [_Attr("db.properties"), _Attr("cache.bin")],
    }
    monkeypatch.setattr(client, "_open", lambda conn: _FakeClient(_FakeSFTP(tree)))

    conn = {"root_path": root, "include_globs": "*.conf,*.yml,*.properties", "exclude_globs": ""}
    files = client.list_files(conn, subpath="", recursive=True)
    paths = {f["path"] for f in files}
    assert paths == {"nginx.conf", "app.yml", "sub/db.properties"}
    # png e bin esclusi dal filtro; nessun path fuori dalla radice.
    assert all(not p.startswith("/") and ".." not in p for p in paths)


def test_list_files_non_recursive_stays_top_level(monkeypatch):
    root = "/etc/askme"
    tree = {
        root: [_Attr("a.conf"), _Attr("sub", is_dir=True)],
        posixpath.join(root, "sub"): [_Attr("b.conf")],
    }
    monkeypatch.setattr(client, "_open", lambda conn: _FakeClient(_FakeSFTP(tree)))
    conn = {"root_path": root, "include_globs": "*.conf", "exclude_globs": ""}
    files = client.list_files(conn, recursive=False)
    assert {f["path"] for f in files} == {"a.conf"}


# ── tetto configurabile sull'elenco ───────────────────────────────────────────

def test_list_files_per_call_limit_caps_entries(monkeypatch):
    root = "/etc/askme"
    tree = {root: [_Attr(f"f{i}.conf") for i in range(5)]}
    monkeypatch.setattr(client, "_open", lambda conn: _FakeClient(_FakeSFTP(tree)))
    conn = {"root_path": root, "include_globs": "*.conf", "exclude_globs": ""}
    files = client.list_files(conn, limit=2)
    assert len(files) == 2


def test_list_files_default_cap_from_settings(monkeypatch):
    from src import config

    monkeypatch.setattr(
        config, "get_settings", lambda: type("S", (), {"ssh_list_max_entries": 3})()
    )
    root = "/etc/askme"
    tree = {root: [_Attr(f"f{i}.conf") for i in range(10)]}
    monkeypatch.setattr(client, "_open", lambda conn: _FakeClient(_FakeSFTP(tree)))
    conn = {"root_path": root, "include_globs": "*.conf", "exclude_globs": ""}
    # Nessun limit esplicito → vale il default configurato.
    assert len(client.list_files(conn)) == 3
    # Un limit per-chiamata scavalca il default.
    assert len(client.list_files(conn, limit=7)) == 7


def test_list_cap_never_exceeds_hard_ceiling(monkeypatch):
    from src import config

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: type("S", (), {"ssh_list_max_entries": 10 ** 9})(),
    )
    # Né il default né un limit assurdo possono superare il tetto assoluto.
    assert client._list_cap(None) == client.SSH_LIST_HARD_CAP
    assert client._list_cap(10 ** 9) == client.SSH_LIST_HARD_CAP
