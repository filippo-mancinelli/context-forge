"""Accesso realtime a file su host remoti via SFTP (lettura e scrittura confinata).

Garanzie di sicurezza:
- lettura (listdir, stat, get) e un'unica operazione di scrittura confinata
  (``write_file``): nessuna cancellazione, nessuna esecuzione di comandi remoti;
- ogni path è confinato sotto ``root_path`` della sorgente (niente escape con
  ``..`` o link assoluti);
- ``write_file`` è atomica (file temporaneo + rename) e crea le directory
  intermedie mancanti, sempre restando confinata sotto la radice;
- limiti espliciti su dimensione del file letto e numero di risultati elencati.
"""
from __future__ import annotations

import posixpath
import re
import stat
from collections import deque
from fnmatch import fnmatch
from typing import Any, Optional

# Limiti prudenti: un tool MCP non deve poter aspirare interi filesystem.
MAX_READ_BYTES = 1_000_000
MAX_WRITE_BYTES = 1_000_000
# La ricerca attraversa il file a blocchi: puo' guardare molto piu' di quanto
# una lettura restituisce, perche' escono solo le righe che corrispondono.
MAX_GREP_BYTES = 50_000_000
GREP_CHUNK_BYTES = 262_144
MAX_GREP_LINE_CHARS = 2_000
GREP_HARD_CAP = 1_000
# Tetto di sicurezza assoluto sull'elenco file: nessuna chiamata puo' superarlo,
# nemmeno passando un ``limit`` piu' alto. Il default effettivo (piu' basso) e'
# configurabile via ``settings.ssh_list_max_entries``.
SSH_LIST_HARD_CAP = 50_000
CONNECT_TIMEOUT = 15


class SSHSourceError(Exception):
    pass


def _open(conn: dict[str, Any]):
    """Apre una connessione SSH+SFTP dai parametri (segreti già in chiaro)."""
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict[str, Any] = {
        "hostname": conn["host"],
        "port": int(conn.get("port") or 22),
        "username": conn["username"],
        "timeout": CONNECT_TIMEOUT,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if conn.get("auth_method") == "key":
        from io import StringIO

        pem = conn.get("private_key") or ""
        last_err: Optional[Exception] = None
        pkey = None
        for loader in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
            try:
                pkey = loader.from_private_key(StringIO(pem))
                break
            except Exception as e:  # noqa: BLE001 - prova il tipo successivo
                last_err = e
        if pkey is None:
            raise SSHSourceError(f"Invalid SSH private key: {last_err}")
        kwargs["pkey"] = pkey
    else:
        kwargs["password"] = conn.get("password") or ""

    try:
        client.connect(**kwargs)
    except Exception as e:  # noqa: BLE001
        raise SSHSourceError(f"SSH connection failed: {e}") from e
    return client


def _confine(root: str, rel: str) -> str:
    """Risolve ``rel`` sotto ``root`` rifiutando qualsiasi escape dalla radice."""
    root_norm = posixpath.normpath(root)
    candidate = posixpath.normpath(posixpath.join(root_norm, rel.lstrip("/")))
    if candidate != root_norm and not candidate.startswith(root_norm + "/"):
        raise SSHSourceError("Path escapes the source root")
    return candidate


def _globs(csv: Optional[str]) -> list[str]:
    return [p.strip() for p in (csv or "").split(",") if p.strip()]


def _matches(name: str, includes: list[str], excludes: list[str]) -> bool:
    if any(fnmatch(name, pat) for pat in excludes):
        return False
    if not includes:
        return True
    return any(fnmatch(name, pat) for pat in includes)


def _list_cap(limit: Optional[int]) -> int:
    """Numero massimo di voci da elencare: ``limit`` per-chiamata se valido,
    altrimenti il default configurato, comunque sotto il tetto assoluto."""
    from ..config import get_settings

    default = get_settings().ssh_list_max_entries
    cap = limit if limit and limit > 0 else default
    return min(cap, SSH_LIST_HARD_CAP)


def list_files(
    conn: dict[str, Any],
    subpath: str = "",
    recursive: bool = False,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Elenca i file (non directory) sotto root/subpath filtrati dai glob.

    ``limit`` alza o abbassa il tetto di voci per questa chiamata; se assente
    vale il default ``settings.ssh_list_max_entries``. In ogni caso non si
    supera ``SSH_LIST_HARD_CAP``.
    """
    root = conn["root_path"]
    includes = _globs(conn.get("include_globs"))
    excludes = _globs(conn.get("exclude_globs"))
    base = _confine(root, subpath)
    cap = _list_cap(limit)

    client = _open(conn)
    out: list[dict[str, Any]] = []
    try:
        sftp = client.open_sftp()
        stack = [base]
        while stack and len(out) < cap:
            current = stack.pop()
            try:
                entries = sftp.listdir_attr(current)
            except IOError:
                continue
            for attr in entries:
                full = posixpath.join(current, attr.filename)
                if stat.S_ISDIR(attr.st_mode or 0):
                    if recursive:
                        stack.append(full)
                    continue
                if not _matches(attr.filename, includes, excludes):
                    continue
                out.append({
                    "path": posixpath.relpath(full, root),
                    "name": attr.filename,
                    "size": attr.st_size,
                    "modified": attr.st_mtime,
                })
                if len(out) >= cap:
                    break
    finally:
        client.close()
    out.sort(key=lambda f: f["path"])
    return out


def _readable_target(conn: dict[str, Any], path: str) -> str:
    """Path assoluto confinato sotto la radice e non escluso dai glob."""
    target = _confine(conn["root_path"], path)
    excludes = _globs(conn.get("exclude_globs"))
    if any(fnmatch(posixpath.basename(target), pat) for pat in excludes):
        raise SSHSourceError("File is excluded by the source configuration")
    return target


def _stat_file(sftp, target: str):
    st = sftp.stat(target)
    if stat.S_ISDIR(st.st_mode or 0):
        raise SSHSourceError("Path is a directory")
    return st


def read_file(
    conn: dict[str, Any],
    path: str,
    offset: Optional[int] = None,
    tail: Optional[int] = None,
) -> dict[str, Any]:
    """Legge una finestra di un file di testo confinato sotto la radice.

    Senza argomenti legge dall'inizio fino a ``MAX_READ_BYTES``. Su un file piu'
    grande del limite servono le due finestre: ``tail`` prende gli ultimi N byte
    (le righe piu' recenti di un log), ``offset`` riprende da una posizione,
    tipicamente ``offset + bytes_read`` della lettura precedente.
    """
    root = conn["root_path"]
    target = _readable_target(conn, path)
    if offset is not None and tail is not None:
        raise SSHSourceError("Use either offset or tail, not both")
    if tail is not None and int(tail) <= 0:
        raise SSHSourceError("tail must be a positive number of bytes")
    if offset is not None and int(offset) < 0:
        raise SSHSourceError("offset must be zero or positive")

    client = _open(conn)
    try:
        sftp = client.open_sftp()
        st = _stat_file(sftp, target)
        size = st.st_size or 0
        window = MAX_READ_BYTES
        if tail is not None:
            window = min(int(tail), MAX_READ_BYTES)
            start = max(0, size - window)
        else:
            start = min(int(offset or 0), size)
        with sftp.open(target, "r") as fh:
            if start:
                fh.seek(start)
            data = fh.read(window)
    finally:
        client.close()

    if not isinstance(data, (bytes, bytearray)):
        data = str(data).encode("utf-8", errors="replace")
    text = data.decode("utf-8", errors="replace")
    return {
        "path": posixpath.relpath(target, root),
        "size": size,
        "offset": start,
        "bytes_read": len(data),
        "truncated": start + len(data) < size,
        "content": text,
    }


def grep_file(
    conn: dict[str, Any],
    path: str,
    pattern: str,
    regex: bool = False,
    ignore_case: bool = True,
    max_matches: int = 50,
) -> dict[str, Any]:
    """Cerca un pattern nelle righe di un file remoto, senza scaricarlo tutto.

    Il file viene letto a blocchi fino a ``MAX_GREP_BYTES`` e solo le righe che
    corrispondono attraversano la rete verso il chiamante. Quando le occorrenze
    superano ``max_matches`` si tengono le ultime: su un log sono quelle che
    interessano.
    """
    target = _readable_target(conn, path)
    if not pattern:
        raise SSHSourceError("pattern must not be empty")
    if max_matches <= 0:
        raise SSHSourceError("max_matches must be positive")
    max_matches = min(int(max_matches), GREP_HARD_CAP)

    flags = re.IGNORECASE if ignore_case else 0
    try:
        matcher = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as e:
        raise SSHSourceError(f"Invalid regular expression: {e}")

    matches: deque[dict[str, Any]] = deque(maxlen=max_matches)
    match_count = 0
    scanned = 0
    line_no = 0
    pending = b""
    truncated = False

    client = _open(conn)
    try:
        sftp = client.open_sftp()
        st = _stat_file(sftp, target)
        size = st.st_size or 0
        with sftp.open(target, "r") as fh:
            while scanned < MAX_GREP_BYTES:
                chunk = fh.read(min(GREP_CHUNK_BYTES, MAX_GREP_BYTES - scanned))
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    chunk = str(chunk).encode("utf-8", errors="replace")
                scanned += len(chunk)
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for raw in lines:
                    line_no += 1
                    text = raw.decode("utf-8", errors="replace").rstrip("\r")
                    if matcher.search(text):
                        match_count += 1
                        matches.append({"line": line_no, "text": text[:MAX_GREP_LINE_CHARS]})
        if pending and scanned < MAX_GREP_BYTES:
            line_no += 1
            text = pending.decode("utf-8", errors="replace").rstrip("\r")
            if matcher.search(text):
                match_count += 1
                matches.append({"line": line_no, "text": text[:MAX_GREP_LINE_CHARS]})
        truncated = scanned < size
    finally:
        client.close()

    return {
        "path": posixpath.relpath(target, conn["root_path"]),
        "size": size,
        "matches": list(matches),
        "match_count": match_count,
        "capped": match_count > len(matches),
        "scanned_bytes": scanned,
        "truncated": truncated,
    }


def write_file(conn: dict[str, Any], path: str, content: str) -> dict[str, Any]:
    """Crea o sovrascrive un file di testo sotto la radice (scrittura atomica).

    Il path è confinato sotto ``root_path`` con lo stesso ``_confine`` usato da
    ``read_file``; le directory intermedie mancanti vengono create. La
    scrittura avviene su un file temporaneo affiancato al target, poi
    rinominato (``posix_rename``) sopra di esso: eventuali lettori concorrenti
    non vedono mai un file parziale. La cancellazione non è supportata.
    """
    import io

    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ValueError(f"content exceeds {MAX_WRITE_BYTES} bytes")

    # Normalizzato una sola volta e riusato sia per _confine sia per
    # _ensure_dirs: se root_path arriva con uno slash finale (mai rifiutato a
    # monte), un confronto contro la stringa grezza non collima mai con la
    # radice normalizzata prodotta da _confine, e il walk-up delle directory
    # mancanti supera la radice creando cartelle fuori da root_path.
    root = posixpath.normpath(conn["root_path"])
    try:
        target = _confine(root, path)
    except SSHSourceError as e:
        raise ValueError(str(e)) from e

    client = _open(conn)
    try:
        sftp = client.open_sftp()
        parent = posixpath.dirname(target)
        _ensure_dirs(sftp, root, parent)
        tmp = f"{target}.context-forge-tmp"
        sftp.putfo(io.BytesIO(data), tmp, confirm=True)
        sftp.posix_rename(tmp, target)
    finally:
        client.close()
    return {"path": posixpath.relpath(target, root), "bytes_written": len(data)}


def _ensure_dirs(sftp, root: str, parent: str) -> None:
    """Crea le directory mancanti tra ``root`` (esclusa) e ``parent`` (inclusa).

    Seconda barriera esplicita oltre a ``_confine``: ``parent`` deriva da un
    target già confinato, ma se il target coincide con la radice stessa il suo
    dirname uscirebbe da ``root`` (es. scrivere con ``path=""``), e questo
    controllo lo intercetta.
    """
    if not parent.startswith(root):
        raise ValueError("Directory escapes the source root")
    missing = []
    current = parent
    while current and current != root:
        try:
            sftp.stat(current)
            break
        except FileNotFoundError:
            missing.append(current)
            current = posixpath.dirname(current)
    for directory in reversed(missing):
        sftp.mkdir(directory)


def check_connection(conn: dict[str, Any]) -> None:
    """Verifica connessione + esistenza della radice; solleva su errore."""
    client = _open(conn)
    try:
        sftp = client.open_sftp()
        sftp.stat(conn["root_path"])
    except SSHSourceError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SSHSourceError(f"Cannot access root path: {e}") from e
    finally:
        client.close()
