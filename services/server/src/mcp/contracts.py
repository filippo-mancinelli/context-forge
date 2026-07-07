"""MCP tools for API contracts (OpenAPI specs and GraphQL schemas)."""
from __future__ import annotations

import logging
from typing import Optional

from .server import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def api_list() -> dict:
    """List the API contracts (OpenAPI specs / GraphQL schemas) available to this organization.

    Returns contract names to use with api_endpoints and api_get_endpoint.

    Returns:
        dict with contracts (name, type, title, version, endpoint_count, status)
    """
    from ..contracts import service
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        contracts = await service.list_contracts(org_id)
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
async def api_endpoints(
    contract: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """List or search API operations across ingested contracts.

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
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        endpoints = await service.list_endpoints(
            org_id, contract_ref=contract, tag=tag, search=search, limit=max(1, min(limit, 500))
        )
    except Exception as e:  # noqa: BLE001
        logger.error("api_endpoints failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", "endpoints": endpoints, "count": len(endpoints)}


@mcp.tool()
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
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        endpoint = await service.get_endpoint(org_id, contract, method, path)
    except ContractNotFoundError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.error("api_get_endpoint failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **endpoint}
