"""Org-scoped service layer for API contracts (OpenAPI + GraphQL)."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from ..db import get_pool
from .graphql import GraphQLIngestError, fetch_introspection, parse_introspection
from .openapi import SpecParseError, parse_openapi

logger = logging.getLogger(__name__)

CONTRACT_TYPES = ("openapi", "graphql")

_CONTRACT_FIELDS = (
    "id, org_id, name, type, source_url, description, title, version, status, "
    "error_message, endpoint_count, fetched_at, created_at, updated_at"
)


class ContractNotFoundError(Exception):
    pass


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    for key in ("fetched_at", "created_at", "updated_at"):
        if d.get(key) is not None:
            d[key] = d[key].isoformat()
    return d


async def list_contracts(org_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_CONTRACT_FIELDS} FROM api_contracts WHERE org_id=$1 ORDER BY name",
            org_id,
        )
    return [_row_to_dict(r) for r in rows]


async def get_contract(org_id: int, ref: int | str) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            row = await conn.fetchrow(
                f"SELECT {_CONTRACT_FIELDS} FROM api_contracts WHERE org_id=$1 AND id=$2",
                org_id, int(ref),
            )
        else:
            row = await conn.fetchrow(
                f"SELECT {_CONTRACT_FIELDS} FROM api_contracts WHERE org_id=$1 AND name=$2",
                org_id, ref,
            )
    if row is None:
        raise ContractNotFoundError(f"API contract '{ref}' not found")
    return _row_to_dict(row)


async def _fetch_spec_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _parse(contract_type: str, raw_spec: str) -> dict[str, Any]:
    if contract_type == "openapi":
        return parse_openapi(raw_spec)
    return parse_introspection(raw_spec)


async def _store_parse_result(
    org_id: int, contract_id: int, raw_spec: str, parsed: dict[str, Any]
) -> None:
    endpoints = parsed["endpoints"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM api_endpoints WHERE contract_id=$1", contract_id)
            for ep in endpoints:
                await conn.execute(
                    """
                    INSERT INTO api_endpoints
                        (contract_id, org_id, method, path, operation_id, summary,
                         description, tags, deprecated, request_schema, response_schema)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb)
                    ON CONFLICT (contract_id, method, path) DO NOTHING
                    """,
                    contract_id, org_id,
                    ep["method"], ep["path"], ep.get("operation_id"),
                    ep.get("summary"), ep.get("description"),
                    ep.get("tags") or [], bool(ep.get("deprecated")),
                    json.dumps(ep.get("request_schema") or {}),
                    json.dumps(ep.get("response_schema") or {}),
                )
            await conn.execute(
                """
                UPDATE api_contracts
                SET raw_spec=$2, title=$3, version=$4, status='ready', error_message=NULL,
                    endpoint_count=$5, fetched_at=NOW(), updated_at=NOW()
                WHERE id=$1
                """,
                contract_id, raw_spec,
                parsed.get("title"), parsed.get("version"), len(endpoints),
            )


async def _mark_error(contract_id: int, error: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE api_contracts SET status='error', error_message=$2, updated_at=NOW() WHERE id=$1",
            contract_id, error[:2000],
        )


async def ingest(org_id: int, contract_id: int, contract_type: str, source_url: Optional[str], raw_spec: Optional[str]) -> dict[str, Any]:
    """Fetch (if needed), parse, and store a contract's endpoints."""
    try:
        if not raw_spec:
            if not source_url:
                raise SpecParseError("Either a source URL or the spec content is required")
            if contract_type == "graphql":
                payload = await fetch_introspection(source_url)
                raw_spec = json.dumps(payload.get("data") or payload)
            else:
                raw_spec = await _fetch_spec_text(source_url)
        parsed = _parse(contract_type, raw_spec)
        if not parsed["endpoints"]:
            raise SpecParseError("Parsed successfully but no operations were found")
        await _store_parse_result(org_id, contract_id, raw_spec, parsed)
    except (SpecParseError, GraphQLIngestError, httpx.HTTPError) as e:
        await _mark_error(contract_id, str(e))
    except Exception as e:  # noqa: BLE001
        logger.error("contract ingest failed: %s", e)
        await _mark_error(contract_id, str(e))
    return await get_contract(org_id, contract_id)


async def create_contract(org_id: int, data: dict[str, Any]) -> dict[str, Any]:
    if data.get("type") not in CONTRACT_TYPES:
        raise ValueError(f"Unsupported contract type '{data.get('type')}'. Supported: {', '.join(CONTRACT_TYPES)}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        contract_id = await conn.fetchval(
            """
            INSERT INTO api_contracts (org_id, name, type, source_url, description)
            VALUES ($1, $2, $3, $4, $5) RETURNING id
            """,
            org_id, data["name"], data["type"],
            data.get("source_url") or None, data.get("description"),
        )
    return await ingest(org_id, contract_id, data["type"], data.get("source_url"), data.get("raw_spec"))


async def refresh_contract(org_id: int, contract_id: int, raw_spec: Optional[str] = None) -> dict[str, Any]:
    """Re-fetch from the source URL (or re-parse pasted content) and re-index endpoints."""
    contract = await get_contract(org_id, contract_id)
    if not raw_spec and not contract.get("source_url"):
        # Pasted spec with no URL: re-parse the stored document.
        pool = await get_pool()
        async with pool.acquire() as conn:
            raw_spec = await conn.fetchval(
                "SELECT raw_spec FROM api_contracts WHERE id=$1", contract_id
            )
    return await ingest(org_id, contract_id, contract["type"], contract.get("source_url"), raw_spec)


async def delete_contract(org_id: int, contract_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM api_contracts WHERE org_id=$1 AND id=$2 RETURNING id",
            org_id, contract_id,
        )
    if deleted is None:
        raise ContractNotFoundError(f"API contract '{contract_id}' not found")


def _endpoint_row(row: Any, include_schemas: bool) -> dict[str, Any]:
    d = dict(row)
    for key in ("request_schema", "response_schema"):
        if key in d:
            if include_schemas:
                if isinstance(d[key], str):
                    d[key] = json.loads(d[key] or "{}")
            else:
                d.pop(key)
    return d


async def list_endpoints(
    org_id: int,
    contract_ref: Optional[int | str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List/search endpoints; without contract_ref searches across all contracts."""
    contract_id = None
    if contract_ref is not None:
        contract_id = (await get_contract(org_id, contract_ref))["id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT e.id, c.name AS contract, e.method, e.path, e.operation_id,
                   e.summary, e.tags, e.deprecated
            FROM api_endpoints e
            JOIN api_contracts c ON c.id = e.contract_id
            WHERE e.org_id = $1
              AND ($2::bigint IS NULL OR e.contract_id = $2)
              AND ($3::text IS NULL OR $3 = ANY(e.tags))
              AND ($4::text IS NULL OR
                   e.path ILIKE '%' || $4 || '%' OR
                   e.operation_id ILIKE '%' || $4 || '%' OR
                   e.summary ILIKE '%' || $4 || '%' OR
                   e.description ILIKE '%' || $4 || '%' OR
                   EXISTS (SELECT 1 FROM unnest(e.tags) t WHERE t ILIKE '%' || $4 || '%'))
            ORDER BY c.name, e.path, e.method
            LIMIT $5
            """,
            org_id, contract_id, tag, search, limit,
        )
    return [_endpoint_row(r, include_schemas=False) for r in rows]


async def get_endpoint(
    org_id: int, contract_ref: int | str, method: str, path: str
) -> dict[str, Any]:
    contract = await get_contract(org_id, contract_ref)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, method, path, operation_id, summary, description, tags,
                   deprecated, request_schema, response_schema
            FROM api_endpoints
            WHERE contract_id=$1 AND upper(method)=upper($2) AND path=$3
            """,
            contract["id"], method, path,
        )
    if row is None:
        raise ContractNotFoundError(
            f"Endpoint {method.upper()} {path} not found in contract '{contract['name']}'"
        )
    d = _endpoint_row(row, include_schemas=True)
    d["contract"] = contract["name"]
    return d
