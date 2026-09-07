"""Scrittura atomica di file su host remoti via SFTP, confinata alla radice.

La connessione SFTP è simulata: si verifica la logica di scrittura atomica
(file temporaneo + rename) e di confinamento del path, non un accesso reale.
"""
import pytest

from src.ssh_sources import client


class _FakeSftp:
    def __init__(self):
        self.written = {}
        self.renames = []
        self.dirs = set()
        self.mkdir_calls = []

    def putfo(self, fobj, path, confirm=True):
        self.written[path] = fobj.read()

    def posix_rename(self, src, dst):
        self.renames.append((src, dst))
        self.written[dst] = self.written.pop(src)

    def stat(self, path):
        if path not in self.dirs:
            raise FileNotFoundError(path)

    def mkdir(self, path):
        self.mkdir_calls.append(path)
        self.dirs.add(path)

    def close(self):
        pass


class _FakeClientCtx:
    def __init__(self, sftp):
        self._sftp = sftp

    def open_sftp(self):
        return self._sftp

    def close(self):
        pass


def _conn():
    return {"root_path": "/etc/app", "host": "h", "port": 22, "username": "u",
            "auth_method": "password", "password": "p", "include_globs": None,
            "exclude_globs": None}


def test_write_file_is_atomic_and_confined(monkeypatch):
    sftp = _FakeSftp()
    sftp.dirs.add("/etc/app")
    monkeypatch.setattr(client, "_open", lambda conn: _FakeClientCtx(sftp))

    out = client.write_file(_conn(), "conf.d/app.yml", "key: value\n")
    assert out["path"] == "conf.d/app.yml"
    assert out["bytes_written"] == len(b"key: value\n")
    # Scrittura atomica: prima il temporaneo, poi il rename sul target.
    assert sftp.renames and sftp.renames[0][1] == "/etc/app/conf.d/app.yml"
    assert sftp.renames[0][0] != sftp.renames[0][1]


def test_write_file_rejects_escape(monkeypatch):
    sftp = _FakeSftp()
    monkeypatch.setattr(client, "_open", lambda conn: _FakeClientCtx(sftp))
    with pytest.raises(ValueError):
        client.write_file(_conn(), "../../etc/passwd", "x")


def test_write_file_ensure_dirs_stays_within_normalized_root(monkeypatch):
    """root_path con slash finale e albero assente: prima della fix, il
    confronto tra la radice grezza e quella normalizzata non collimava mai e
    il walk-up delle directory mancanti superava la radice, creando cartelle
    fuori da root_path (es. /data/projects)."""
    sftp = _FakeSftp()  # dirs vuoto: stat fallisce per qualunque path
    monkeypatch.setattr(client, "_open", lambda conn: _FakeClientCtx(sftp))
    conn = _conn()
    conn["root_path"] = "/data/projects/myapp/"

    client.write_file(conn, "sub/file.txt", "x")

    assert sftp.mkdir_calls
    for created in sftp.mkdir_calls:
        assert created.startswith("/data/projects/myapp")
        assert created != "/data/projects"
        assert created != "/data"


def test_write_file_rejects_oversize_content(monkeypatch):
    def _boom(conn):
        raise AssertionError("must not open the SSH connection before the size check")

    monkeypatch.setattr(client, "_open", _boom)
    with pytest.raises(ValueError, match="exceeds"):
        client.write_file(_conn(), "big.txt", "x" * (client.MAX_WRITE_BYTES + 1))


def test_write_file_returns_confined_relative_path(monkeypatch):
    sftp = _FakeSftp()
    sftp.dirs.add("/etc/app")
    monkeypatch.setattr(client, "_open", lambda conn: _FakeClientCtx(sftp))

    out = client.write_file(_conn(), "./conf.d/../app.yml", "key: value\n")
    assert out["path"] == "app.yml"
    assert not sftp.mkdir_calls
