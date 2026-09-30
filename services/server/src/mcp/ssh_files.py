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
from .permissions import PermissionDenied, requires_permission
from .context import get_current_user_id, resolve_org_id, require_project_id
from .project_access import selection_rights
from .source_links import ui_link
from ..catalog import machines, selections
from ..catalog.names import machine_name
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
    """List the SSH folders selected for the active project.

    Each folder points at a directory on a machine of the organization catalog.
    Use ssh_list_files / ssh_read_file / ssh_grep with a folder's name or id.
    Folders not selected yet are listed by catalog_list.
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
                "machine": s["machine_name"],
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
@requires_permission("ssh-write", alternatives=("context-write",))
async def ssh_write_file(source: str, path: str, content: str, reason: str = "") -> dict:
    """Create or overwrite a text file on an SSH source (confined to its root).

    The write is atomic (temp file + rename) and creates any missing
    intermediate directories. Deleting files is not supported.

    Without the ssh-write permission nothing is written: the content is stored
    as a write request with a size/hash preview for an organization admin to
    approve. Poll the outcome with write_request_status.

    Args:
        source: source name or id (from ssh_sources).
        path: file path relative to the source root.
        content: full text content to write.
        reason: why the change is needed; shown to the approver.

    Returns:
        dict with the written `path` and `bytes_written`.
    """
    from ..ssh_sources import client
    from ..ssh_sources.service import decrypted_conn
    from .permissions import has_permission

    try:
        record = await _resolve(source, include_secret=True)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}

    if not has_permission("ssh-write"):
        return await _propose_ssh_write(record, path, content, reason)

    try:
        result = await asyncio.to_thread(
            client.write_file, decrypted_conn(record), path, content
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    return {"source": record["name"], **result}


async def _propose_ssh_write(record: dict, path: str, content: str, reason: str) -> dict:
    """Store the content as a pending write request instead of writing it."""
    from .. import write_previews, write_requests
    from ..ssh_sources import client
    from .approvals import current_requester, pending_response
    from .audit import scrub_text

    conn_params = ssh_service.decrypted_conn(record)
    try:
        client.confined_path(conn_params, path)
    except Exception as e:  # noqa: BLE001 - same rejection the direct path gives
        return {"status": "error", "error": str(e)}

    try:
        preview = await asyncio.to_thread(
            write_previews.file_preview, conn_params, path, content
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    if preview.get("read_error"):
        preview["read_error"] = scrub_text(preview["read_error"])

    # The catalog record carries no project: the request belongs to the active one.
    project_id = await require_project_id()
    kind, requester_id, label = current_requester()
    created = await write_requests.create(
        org_id=record["org_id"],
        project_id=project_id,
        kind="ssh_write_file",
        target=f"{record['name']}:{path}",
        payload={"source": record["name"], "path": path, "content": content},
        preview=preview,
        reason=reason,
        requested_by_kind=kind,
        requested_by_id=requester_id,
        requested_by=label,
    )
    return pending_response(created["id"], preview)


@mcp.tool()
@requires_permission("sources-write")
async def ssh_source_add(
    name: str,
    root_path: str,
    machine: Optional[str] = None,
    host: Optional[str] = None,
    username: Optional[str] = None,
    port: int = 22,
    auth_method: str = "password",
    include_globs: Optional[str] = None,
    exclude_globs: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Register an SSH folder in the organization catalog and select it for the active project.

    Pass the name of a catalog machine, or host and username. An unknown address
    creates a machine without credential ('pending_secret'): a person adds the
    password or key from the Catalog page before files can be read. When the
    same machine and root are already registered, the existing folder is
    selected instead of creating a copy. This tool takes no credential.

    Args:
        name: folder name, unique in the organization.
        root_path: directory every read is confined to.
        machine: name of a machine in the catalog.
        host: host to read files from, when no machine name is given.
        username: user the connection authenticates as, with host.
        port: SSH port, with host. Defaults to 22.
        auth_method: 'password' or 'key' — the credential a person will add.
        include_globs: comma-separated patterns to include.
        exclude_globs: comma-separated patterns to exclude, e.g. secrets.
        description: optional free-text description.

    Returns:
        dict with the folder, whether it was created, and its machine.
    """
    org_id = await resolve_org_id()
    project_id = await require_project_id()
    # Registrare equivale a selezionare: valgono gli stessi diritti di resource_select.
    can_select, can_select_restricted = await selection_rights(org_id, project_id)
    if not can_select:
        raise PermissionDenied("Adding a folder to this project requires the member role on this project")
    try:
        if machine:
            target = await machines.get_machine_by_name(org_id, machine)
            if target is None:
                return {"status": "error", "error": f"Machine '{machine}' is not in the catalog"}
        elif host and username:
            target = await machines.find_machine(org_id, host, port, username)
            if target is None:
                target = await machines.create_machine(org_id, {
                    "name": machine_name(username, host, port),
                    "host": host,
                    "port": port,
                    "username": username,
                    "auth_method": auth_method,
                })
                await machines.mark_pending_secret(org_id, target["id"])
                target = {**target, "status": "pending_secret"}
        else:
            return {"status": "error", "error": "Pass machine, or host and username"}

        source = await ssh_service.find_source(org_id, target["id"], root_path)
        created = source is None
        if created:
            source = await ssh_service.create_source(org_id, {
                "name": name,
                "machine_id": target["id"],
                "root_path": root_path,
                "include_globs": include_globs,
                "exclude_globs": exclude_globs,
                "description": description,
            })
        await selections.select_resource(
            org_id, project_id, "folders", source["id"], get_current_user_id(),
            can_select_restricted,
        )
    except selections.RestrictedResourceError as exc:
        raise PermissionDenied(f"'{exc}' is restricted: only an organization admin can add it to a project")
    except Exception as exc:  # noqa: BLE001 — vincoli di unicità e errori del driver
        return {"status": "error", "error": str(exc)}

    result = {
        "status": "ok",
        "created": created,
        "source": source,
        "machine": {"name": target["name"], "status": target.get("status")},
    }
    if target.get("status") == "pending_secret":
        result["next_step"] = (
            f"Machine '{target['name']}' has no credential yet. Add the password or key "
            "from the Catalog page in the web UI; reads fail until then."
        )
        result["ui_url"] = ui_link("/catalog")
    return result
