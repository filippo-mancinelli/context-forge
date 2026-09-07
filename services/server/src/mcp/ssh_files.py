"""Tool MCP per leggere e scrivere file su host remoti via SSH (realtime).

Espone le sorgenti SSH del progetto attivo e permette di elencarne, leggerne e
scriverne i file (tipicamente configurazioni). Ogni accesso è confinato alla
radice della sorgente; la lettura richiede il permesso ``ssh-read``, la
scrittura richiede ``ssh-write`` (di norma riservato all'owner del progetto).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .server import mcp
from .permissions import requires_permission
from .context import resolve_org_id, require_project_id
from .source_links import ui_link
from ..ssh_sources import service as ssh_service

logger = logging.getLogger(__name__)


async def _resolve(source: str, include_secret: bool = False) -> dict:
    from ..ssh_sources import service

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    if org_id is None or project_id is None:
        raise ValueError("No active project. Select one with use_project first.")
    return await service.resolve_source(org_id, project_id, source, include_secret=include_secret)


@mcp.tool()
@requires_permission("ssh-read")
async def ssh_sources() -> dict:
    """List the SSH file sources configured for the active project.

    Each source points at a directory on a remote Linux host (read-only). Use
    ssh_list_files / ssh_read_file with a source's name or id.
    """
    from ..ssh_sources import service

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    if org_id is None or project_id is None:
        return {"status": "error", "error": "No active project"}
    sources = await service.list_sources(org_id, project_id)
    return {
        "sources": [
            {
                "id": s["id"],
                "name": s["name"],
                "host": s["host"],
                "root_path": s["root_path"],
                "status": s["status"],
            }
            for s in sources
        ]
    }


@mcp.tool()
@requires_permission("ssh-read")
async def ssh_list_files(
    source: str, subpath: str = "", recursive: bool = True, limit: Optional[int] = None
) -> dict:
    """List files under an SSH source (filtered by its include/exclude globs).

    Descends into subdirectories by default, confined to the source root. Pass
    recursive=false to list only the top level.

    Args:
        source: source name or id (from ssh_sources).
        subpath: optional directory under the source root.
        recursive: descend into subdirectories (default true).
        limit: max entries to return. Defaults to the server setting
            (ssh_list_max_entries); raise it here when a source holds more
            files than the default cap. A hard safety ceiling still applies.

    Returns:
        dict with `files` (path relative to the source root, name, size, modified).
    """
    from ..ssh_sources import client

    try:
        record = await _resolve(source, include_secret=True)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    from ..ssh_sources.service import decrypted_conn

    try:
        files = await asyncio.to_thread(
            client.list_files, decrypted_conn(record), subpath, recursive, limit
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    return {"source": record["name"], "files": files}


@mcp.tool()
@requires_permission("ssh-read")
async def ssh_read_file(
    source: str,
    path: str,
    tail_bytes: Optional[int] = None,
    offset: Optional[int] = None,
) -> dict:
    """Read a text file from an SSH source (confined to the source root).

    Only a window of ~1 MB is returned. On a file bigger than that, reading from
    the start gives the oldest content: for a log, pass tail_bytes to get the
    most recent lines instead, or offset to resume where a previous read stopped
    (`offset` + `bytes_read` of that result). To find something in a large file
    without pulling it over, use ssh_grep.

    Args:
        source: source name or id (from ssh_sources).
        path: file path relative to the source root.
        tail_bytes: read the last N bytes instead of the first ones.
        offset: byte position to start reading from. Not combinable with tail_bytes.

    Returns:
        dict with the file `content`, `size`, the `offset` it starts at,
        `bytes_read`, and `truncated` (true when content stops before EOF).
    """
    from ..ssh_sources import client
    from ..ssh_sources.service import decrypted_conn

    try:
        record = await _resolve(source, include_secret=True)
        result = await asyncio.to_thread(
            client.read_file, decrypted_conn(record), path, offset, tail_bytes
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    return {"source": record["name"], **result}


@mcp.tool()
@requires_permission("ssh-read")
async def ssh_grep(
    source: str,
    path: str,
    pattern: str,
    regex: bool = False,
    ignore_case: bool = True,
    max_matches: int = 50,
) -> dict:
    """Search a pattern in a remote file and return only the matching lines.

    Scans the file server-side in blocks, so it reaches well past the 1 MB read
    window of ssh_read_file: use it on log files. When matches exceed
    max_matches the most recent ones are kept.

    Args:
        source: source name or id (from ssh_sources).
        path: file path relative to the source root.
        pattern: text to look for (or a regular expression when regex=true).
        regex: treat pattern as a Python regular expression (default false).
        ignore_case: case-insensitive search (default true).
        max_matches: how many matching lines to return (default 50).

    Returns:
        dict with `matches` (line number + text), `match_count`, `capped`,
        `scanned_bytes` and `truncated` (true when the scan ceiling was hit).
    """
    from ..ssh_sources import client
    from ..ssh_sources.service import decrypted_conn

    try:
        record = await _resolve(source, include_secret=True)
        result = await asyncio.to_thread(
            client.grep_file,
            decrypted_conn(record),
            path,
            pattern,
            regex,
            ignore_case,
            max_matches,
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    return {"source": record["name"], **result}


@mcp.tool()
@requires_permission("ssh-write")
async def ssh_write_file(source: str, path: str, content: str) -> dict:
    """Create or overwrite a text file on an SSH source (confined to its root).

    The write is atomic (temp file + rename) and creates any missing
    intermediate directories. Deleting files is not supported.

    Args:
        source: source name or id (from ssh_sources).
        path: file path relative to the source root.
        content: full text content to write.

    Returns:
        dict with the written `path` and `bytes_written`.
    """
    from ..ssh_sources import client
    from ..ssh_sources.service import decrypted_conn

    try:
        record = await _resolve(source, include_secret=True)
        result = await asyncio.to_thread(
            client.write_file, decrypted_conn(record), path, content
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    return {"source": record["name"], **result}


@mcp.tool()
@requires_permission("sources-write")
async def ssh_source_add(
    name: str,
    host: str,
    root_path: str,
    username: str,
    port: int = 22,
    auth_method: str = "password",
    include_globs: Optional[str] = None,
    exclude_globs: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Register an SSH file source for the active project, without its credential.

    The source is created in 'pending_secret' state: a person adds the password
    or private key from the web UI before files can be read. This tool takes no
    credential of any kind.

    Args:
        name: unique source name within the project.
        host: host to read files from.
        root_path: directory every read is confined to.
        username: user the connection authenticates as.
        port: SSH port. Defaults to 22.
        auth_method: 'password' or 'key' — which credential will be added later.
        include_globs: comma-separated patterns to include.
        exclude_globs: comma-separated patterns to exclude, e.g. secrets.
        description: optional free-text description.

    Returns:
        dict with the created source and where to add its credential.
    """
    org_id = await resolve_org_id()
    project_id = await require_project_id()
    data = {
        "name": name,
        "host": host,
        "port": port,
        "username": username,
        "auth_method": auth_method,
        "root_path": root_path,
        "include_globs": include_globs,
        "exclude_globs": exclude_globs,
        "description": description,
    }
    try:
        source = await ssh_service.create_source(org_id, project_id, data)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — vincoli di unicità e errori del driver
        return {"status": "error", "error": str(exc)}

    await ssh_service.mark_pending_secret(org_id, project_id, source["id"])
    return {
        "status": "ok",
        "source": {**source, "status": "pending_secret"},
        "next_step": (
            f"Source '{name}' has no credential yet. Add the password or key from "
            "the SSH Files page in the web UI; reads fail until then."
        ),
        "ui_url": ui_link("/ssh-sources"),
    }
