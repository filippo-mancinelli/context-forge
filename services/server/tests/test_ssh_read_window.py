"""Lettura a finestra (tail/offset) e ricerca per riga sulle sorgenti SSH.

Un file di log di produzione supera di molto ``MAX_READ_BYTES``: leggerne solo
il primo megabyte restituisce le righe piu' vecchie, cioe' l'esatto contrario di
quello che serve. Qui si verifica che si possa leggere la coda, riprendere da un
offset e cercare un pattern senza scaricare tutto il file.

La connessione SFTP e' simulata: si verifica la logica di finestra e di
ricerca, non un accesso reale.
"""
import posixpath
import stat as stat_mod

import pytest

from src.ssh_sources import client


class _Attr:
    def __init__(self, size, is_dir=False):
        self.st_size = size
        self.st_mtime = 0
        self.st_mode = (stat_mod.S_IFDIR if is_dir else stat_mod.S_IFREG) | 0o644


class _FakeFile:
    """File SFTP minimale: seek + read, come paramiko.SFTPFile."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def seek(self, pos, whence=0):
        self._pos = pos if whence == 0 else self._pos + pos

    def read(self, size=None):
        end = len(self._data) if size is None else self._pos + size
        chunk = self._data[self._pos : end]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeSFTP:
    def __init__(self, files: dict[str, bytes]):
        self.files = files

    def stat(self, path):
        return _Attr(len(self.files[path]))

    def open(self, path, mode="r"):
        return _FakeFile(self.files[path])


class _FakeClient:
    def __init__(self, sftp):
        self._sftp = sftp

    def open_sftp(self):
        return self._sftp

    def close(self):
        pass


ROOT = "/srv/logs"
LOG_PATH = posixpath.join(ROOT, "application.log")


def _conn():
    return {"root_path": ROOT, "include_globs": "", "exclude_globs": ""}


def _install(monkeypatch, data: bytes):
    sftp = _FakeSFTP({LOG_PATH: data})
    monkeypatch.setattr(client, "_open", lambda conn: _FakeClient(sftp))


# ── finestra di lettura ───────────────────────────────────────────────────────

def test_read_file_defaults_to_the_head_as_before(monkeypatch):
    _install(monkeypatch, b"riga1\nriga2\nriga3\n")
    out = client.read_file(_conn(), "application.log")
    assert out["content"] == "riga1\nriga2\nriga3\n"
    assert out["offset"] == 0
    assert out["truncated"] is False


def test_read_file_tail_returns_the_end_of_the_file(monkeypatch):
    _install(monkeypatch, b"vecchio " * 10 + b"ULTIMA RIGA")
    out = client.read_file(_conn(), "application.log", tail=11)
    assert out["content"] == "ULTIMA RIGA"
    assert out["offset"] == out["size"] - 11
    # La coda arriva a fine file: non c'e' altro da leggere dopo.
    assert out["truncated"] is False


def test_read_file_tail_larger_than_file_returns_everything(monkeypatch):
    _install(monkeypatch, b"corto")
    out = client.read_file(_conn(), "application.log", tail=10_000)
    assert out["content"] == "corto"
    assert out["offset"] == 0


def test_read_file_offset_resumes_from_a_position(monkeypatch):
    _install(monkeypatch, b"0123456789")
    out = client.read_file(_conn(), "application.log", offset=4)
    assert out["content"] == "456789"
    assert out["offset"] == 4
    assert out["bytes_read"] == 6


def test_read_file_truncated_when_window_stops_before_eof(monkeypatch):
    monkeypatch.setattr(client, "MAX_READ_BYTES", 4)
    _install(monkeypatch, b"0123456789")
    out = client.read_file(_conn(), "application.log")
    assert out["content"] == "0123"
    assert out["truncated"] is True
    # L'offset successivo permette di riprendere da dove si e' interrotto.
    assert out["offset"] + out["bytes_read"] == 4


def test_read_file_tail_never_exceeds_the_read_cap(monkeypatch):
    monkeypatch.setattr(client, "MAX_READ_BYTES", 4)
    _install(monkeypatch, b"0123456789")
    out = client.read_file(_conn(), "application.log", tail=10)
    assert out["bytes_read"] == 4
    assert out["content"] == "6789"


def test_read_file_rejects_offset_and_tail_together(monkeypatch):
    _install(monkeypatch, b"0123456789")
    with pytest.raises(client.SSHSourceError):
        client.read_file(_conn(), "application.log", offset=1, tail=1)


def test_read_file_rejects_negative_window(monkeypatch):
    _install(monkeypatch, b"0123456789")
    with pytest.raises(client.SSHSourceError):
        client.read_file(_conn(), "application.log", tail=0)
    with pytest.raises(client.SSHSourceError):
        client.read_file(_conn(), "application.log", offset=-1)


def test_read_file_window_still_confined_and_glob_checked(monkeypatch):
    _install(monkeypatch, b"x")
    with pytest.raises(client.SSHSourceError):
        client.read_file(_conn(), "../../etc/shadow", tail=10)
    conn = {"root_path": ROOT, "include_globs": "", "exclude_globs": "*.log"}
    with pytest.raises(client.SSHSourceError):
        client.read_file(conn, "application.log", tail=10)


# ── ricerca per riga ──────────────────────────────────────────────────────────

LOG = (
    b"2026-08-24 09:00:00 INFO  avvio\n"
    b"2026-08-24 09:01:00 INFO  Standard phase skipped reason=no-operator-capacity\n"
    b"2026-08-24 09:02:00 INFO  altro\n"
    b"2026-08-24 09:03:00 ERROR fallito\n"
    b"2026-08-24 09:04:00 INFO  Standard phase skipped reason=no-operator-capacity\n"
)


def test_grep_file_returns_matching_lines_with_numbers(monkeypatch):
    _install(monkeypatch, LOG)
    out = client.grep_file(_conn(), "application.log", "no-operator-capacity")
    assert [m["line"] for m in out["matches"]] == [2, 5]
    assert out["match_count"] == 2
    assert all("no-operator-capacity" in m["text"] for m in out["matches"])


def test_grep_file_is_case_insensitive_by_default(monkeypatch):
    _install(monkeypatch, LOG)
    assert client.grep_file(_conn(), "application.log", "ERROR")["match_count"] == 1
    assert client.grep_file(_conn(), "application.log", "error")["match_count"] == 1
    out = client.grep_file(_conn(), "application.log", "error", ignore_case=False)
    assert out["match_count"] == 0


def test_grep_file_supports_regex(monkeypatch):
    _install(monkeypatch, LOG)
    out = client.grep_file(_conn(), "application.log", r"^\S+ 09:0[13]:00", regex=True)
    assert [m["line"] for m in out["matches"]] == [2, 4]


def test_grep_file_keeps_the_last_matches_when_capped(monkeypatch):
    _install(monkeypatch, LOG)
    out = client.grep_file(_conn(), "application.log", "INFO", max_matches=2)
    # Su un log interessa la coda: si tengono le occorrenze piu' recenti.
    assert [m["line"] for m in out["matches"]] == [3, 5]
    assert out["match_count"] == 4
    assert out["capped"] is True


def test_grep_file_scans_beyond_the_read_cap(monkeypatch):
    monkeypatch.setattr(client, "MAX_READ_BYTES", 8)
    filler = b"riempitivo\n" * 500
    _install(monkeypatch, filler + b"AGO IN PAGLIAIO\n")
    out = client.grep_file(_conn(), "application.log", "AGO IN PAGLIAIO")
    assert out["match_count"] == 1
    assert out["scanned_bytes"] > client.MAX_READ_BYTES


def test_grep_file_stops_at_the_scan_ceiling(monkeypatch):
    monkeypatch.setattr(client, "MAX_GREP_BYTES", 20)
    _install(monkeypatch, b"a\n" * 100 + b"trovami\n")
    out = client.grep_file(_conn(), "application.log", "trovami")
    assert out["match_count"] == 0
    assert out["truncated"] is True
    assert out["scanned_bytes"] <= 20


def test_grep_file_rejects_an_invalid_regex(monkeypatch):
    _install(monkeypatch, LOG)
    with pytest.raises(client.SSHSourceError):
        client.grep_file(_conn(), "application.log", "([unbalanced", regex=True)


def test_grep_file_is_confined_and_glob_checked(monkeypatch):
    _install(monkeypatch, LOG)
    with pytest.raises(client.SSHSourceError):
        client.grep_file(_conn(), "../../etc/shadow", "root")
    conn = {"root_path": ROOT, "include_globs": "", "exclude_globs": "*.log"}
    with pytest.raises(client.SSHSourceError):
        client.grep_file(conn, "application.log", "INFO")
