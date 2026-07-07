"""GraphQL schema ingestion via the standard introspection query.

A GraphQL contract is created from either a live endpoint URL (we POST the
introspection query) or a pasted introspection-result JSON. Query and mutation
fields become "endpoints"; argument and return types are rendered with named
type references plus a shallow map of the referenced input/object types so an
agent can build a valid operation without the full SDL.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx

INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types {
      kind name description
      fields(includeDeprecated: true) {
        name description isDeprecated deprecationReason
        args { name description type { ...TypeRef } defaultValue }
        type { ...TypeRef }
      }
      inputFields { name description type { ...TypeRef } defaultValue }
      enumValues(includeDeprecated: true) { name description }
    }
  }
}
fragment TypeRef on __Type {
  kind name
  ofType { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
}
"""


class GraphQLIngestError(Exception):
    pass


async def fetch_introspection(url: str, headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json={"query": INTROSPECTION_QUERY}, headers=headers or {})
        resp.raise_for_status()
        payload = resp.json()
    if "errors" in payload and not payload.get("data"):
        raise GraphQLIngestError(f"Introspection rejected: {payload['errors']}")
    return payload


def _render_type(t: Optional[dict[str, Any]]) -> str:
    """Render a TypeRef as SDL-ish text, e.g. [User!]!"""
    if not t:
        return "Unknown"
    kind = t.get("kind")
    if kind == "NON_NULL":
        return f"{_render_type(t.get('ofType'))}!"
    if kind == "LIST":
        return f"[{_render_type(t.get('ofType'))}]"
    return t.get("name") or "Unknown"


def _named_type(t: Optional[dict[str, Any]]) -> Optional[str]:
    while t and t.get("kind") in ("NON_NULL", "LIST"):
        t = t.get("ofType")
    return (t or {}).get("name")


def _shallow_type(type_def: dict[str, Any]) -> dict[str, Any]:
    """A one-level summary of an object/input/enum type."""
    out: dict[str, Any] = {"kind": type_def.get("kind"), "description": type_def.get("description")}
    if type_def.get("fields"):
        out["fields"] = {f["name"]: _render_type(f.get("type")) for f in type_def["fields"]}
    if type_def.get("inputFields"):
        out["fields"] = {f["name"]: _render_type(f.get("type")) for f in type_def["inputFields"]}
    if type_def.get("enumValues"):
        out["values"] = [v["name"] for v in type_def["enumValues"]]
    return out


def parse_introspection(payload: Any) -> dict[str, Any]:
    """Parse an introspection result (or its ``data`` envelope) into endpoints."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            raise GraphQLIngestError(f"Not valid introspection JSON: {e}") from e
    if not isinstance(payload, dict):
        raise GraphQLIngestError("Introspection result must be a JSON object")

    schema = payload.get("__schema") or (payload.get("data") or {}).get("__schema")
    if not schema:
        raise GraphQLIngestError("No '__schema' found in introspection result")

    types = {t["name"]: t for t in schema.get("types") or [] if t.get("name")}
    roots = {
        "QUERY": (schema.get("queryType") or {}).get("name"),
        "MUTATION": (schema.get("mutationType") or {}).get("name"),
        "SUBSCRIPTION": (schema.get("subscriptionType") or {}).get("name"),
    }

    endpoints: list[dict[str, Any]] = []
    for method, root_name in roots.items():
        root = types.get(root_name) if root_name else None
        if not root:
            continue
        for field in root.get("fields") or []:
            args = [
                {
                    "name": a["name"],
                    "type": _render_type(a.get("type")),
                    "description": a.get("description"),
                    "default": a.get("defaultValue"),
                }
                for a in field.get("args") or []
            ]
            # Shallow expansion of the types this field touches, so the
            # endpoint detail is useful on its own.
            referenced: dict[str, Any] = {}
            for name in filter(None, [_named_type(field.get("type"))] + [
                _named_type(a.get("type")) for a in field.get("args") or []
            ]):
                type_def = types.get(name)
                if type_def and type_def.get("kind") in ("OBJECT", "INPUT_OBJECT", "ENUM") and not name.startswith("__"):
                    referenced[name] = _shallow_type(type_def)

            endpoints.append(
                {
                    "method": method,
                    "path": field["name"],
                    "operation_id": field["name"],
                    "summary": field.get("description"),
                    "description": field.get("deprecationReason"),
                    "tags": [],
                    "deprecated": bool(field.get("isDeprecated")),
                    "request_schema": {"args": args},
                    "response_schema": {
                        "type": _render_type(field.get("type")),
                        "types": referenced,
                    },
                }
            )

    return {
        "title": None,
        "version": None,
        "description": None,
        "endpoints": endpoints,
    }
