"""MCP tools for API contracts (OpenAPI specs and GraphQL schemas)."""
from __future__ import annotations

import logging
from typing import Optional

from .server import mcp
from .permissions import requires_permission
from ..contracts import service as contracts_service

logger = logging.getLogger(__name__)


@mcp.tool()
@requires_permission("context-read")
async def api_list() -> dict:
    """List the API contracts (OpenAPI specs / GraphQL schemas) available to the current project.

    Returns contract names to use with api_endpoints and api_get_endpoint.

    Returns:
        dict with contracts (name, type, title, version, endpoint_count, status)
    """
    from ..contracts import service
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    try:
        contracts = await service.list_contracts(org_id, project_id)
    except Exception as e:  # noqa: BLE001
        logger.error("api_list failed: %s", e)
        return {"status": "error", "error": str(e)}
    slim = [
        {
            "name": c["name"],
            "type": c["type"],
            "title": c.get("title"),
            "version": c.get("version"),
            "description": c.get("description"),
            "endpoint_count": c.get("endpoint_count", 0),
            "status": c.get("status"),
        }
        for c in contracts
    ]
    return {"status": "ok", "contracts": slim, "count": len(slim)}


@mcp.tool()
@requires_permission("context-read")
async def api_endpoints(
    contract: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """List or search API operations across the current project's ingested contracts.

    For REST contracts an operation is method+path; for GraphQL it is a
    QUERY/MUTATION field. Use api_get_endpoint for full request/response schemas.

    Args:
        contract: Restrict to one contract by name (from api_list)
        tag: Restrict to one OpenAPI tag
        search: Case-insensitive text matched against path, operationId,
            summary, description, and tags
        limit: Maximum operations to return (default 100)

    Returns:
        dict with endpoints (contract, method, path, operation_id, summary, tags)
    """
    from ..contracts import service
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    try:
        endpoints = await service.list_endpoints(
            org_id, project_id, contract_ref=contract, tag=tag, search=search, limit=max(1, min(limit, 500))
        )
    except Exception as e:  # noqa: BLE001
        logger.error("api_endpoints failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", "endpoints": endpoints, "count": len(endpoints)}


@mcp.tool()
@requires_permission("context-read")
async def api_get_endpoint(contract: str, method: str, path: str) -> dict:
    """Get the full contract of one API operation: parameters, request body, responses.

    Args:
        contract: Contract name (from api_list)
        method: HTTP method (GET, POST, ...) or QUERY/MUTATION for GraphQL
        path: The endpoint path (e.g. /users/{id}) or GraphQL field name

    Returns:
        dict with request_schema (parameters, body) and response_schema
        (per-status schemas, or GraphQL return type plus referenced types)
    """
    from ..contracts import service
    from ..contracts.service import ContractNotFoundError
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    try:
        endpoint = await service.get_endpoint(org_id, project_id, contract, method, path)
    except ContractNotFoundError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.error("api_get_endpoint failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **endpoint}


@mcp.tool()
@requires_permission("sources-write")
async def api_add(
    name: str,
    type: str,
    source_url: Optional[str] = None,
    raw_spec: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Add an API contract (OpenAPI or GraphQL) to the active project.

    Provide either source_url, which the server fetches and refreshes, or
    raw_spec with the specification content itself.

    Args:
        name: unique contract name within the project.
        type: 'openapi' or 'graphql'.
        source_url: URL of the specification or GraphQL endpoint.
        raw_spec: the specification content, when there is no URL to fetch.
        description: optional free-text description.

    Returns:
        dict with the created contract.
    """
    from .context import require_project_id, resolve_org_id

    if not source_url and not raw_spec:
        return {"status": "error", "error": "Provide a source URL or the spec content"}

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    data = {
        "name": name,
        "type": type,
        "source_url": source_url,
        "raw_spec": raw_spec,
        "description": description,
    }
    try:
        contract = await contracts_service.create_contract(org_id, project_id, data)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — vincolo di unicità e altri errori del driver
        message = str(exc)
        if "api_contracts_project_name_key" in message:
            return {"status": "error", "error": f"Contract '{name}' already exists"}
        return {"status": "error", "error": message}
    return {"status": "ok", "contract": contract}
