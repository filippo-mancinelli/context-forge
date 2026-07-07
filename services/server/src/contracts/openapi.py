"""OpenAPI / Swagger spec parsing.

Accepts OpenAPI 3.x and Swagger 2.0 documents as JSON or YAML text and
extracts one record per operation. Local ``$ref``s are inlined up to a fixed
depth so an endpoint's request/response schema is self-contained without
risking infinite recursion on cyclic models; deeper refs stay as ``{"$ref": name}``.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import yaml

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
MAX_REF_DEPTH = 3


class SpecParseError(Exception):
    pass


def _load_document(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise SpecParseError("Empty specification")
    try:
        if text.startswith("{"):
            doc = json.loads(text)
        else:
            doc = yaml.safe_load(text)
    except Exception as e:  # noqa: BLE001
        raise SpecParseError(f"Not valid JSON or YAML: {e}") from e
    if not isinstance(doc, dict):
        raise SpecParseError("Specification must be a JSON/YAML object")
    if "openapi" not in doc and "swagger" not in doc:
        raise SpecParseError("Missing 'openapi' or 'swagger' version field")
    return doc


def _resolve_ref(doc: dict[str, Any], ref: str) -> Optional[Any]:
    if not ref.startswith("#/"):
        return None  # external refs are kept as-is
    node: Any = doc
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _inline_refs(doc: dict[str, Any], node: Any, depth: int = 0) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if depth >= MAX_REF_DEPTH:
                return {"$ref": ref.rsplit("/", 1)[-1]}
            resolved = _resolve_ref(doc, ref)
            if resolved is None:
                return {"$ref": ref}
            return _inline_refs(doc, resolved, depth + 1)
        return {k: _inline_refs(doc, v, depth) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(doc, item, depth) for item in node]
    return node


def _request_schema(doc: dict[str, Any], operation: dict[str, Any], path_item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}

    # Parameters: path-level ones apply to every operation beneath.
    params = []
    for p in list(path_item.get("parameters") or []) + list(operation.get("parameters") or []):
        p = _inline_refs(doc, p)
        if not isinstance(p, dict):
            continue
        entry = {
            "name": p.get("name"),
            "in": p.get("in"),
            "required": bool(p.get("required")),
            "description": p.get("description"),
            "schema": p.get("schema") or {k: p[k] for k in ("type", "format", "enum") if k in p},
        }
        params.append(entry)
    if params:
        out["parameters"] = params

    # OpenAPI 3.x request body.
    body = operation.get("requestBody")
    if isinstance(body, dict):
        body = _inline_refs(doc, body)
        content = body.get("content") or {}
        media = content.get("application/json") or next(iter(content.values()), None) if content else None
        if isinstance(media, dict) and media.get("schema") is not None:
            out["body"] = media["schema"]
            out["body_required"] = bool(body.get("required"))

    # Swagger 2.0 body parameters land in `parameters` with in=body.
    for p in out.get("parameters", []):
        if p.get("in") == "body" and p.get("schema"):
            out["body"] = p["schema"]

    return out


def _response_schema(doc: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for status, resp in (operation.get("responses") or {}).items():
        resp = _inline_refs(doc, resp)
        if not isinstance(resp, dict):
            continue
        entry: dict[str, Any] = {"description": resp.get("description")}
        content = resp.get("content") or {}
        media = content.get("application/json") or next(iter(content.values()), None) if content else None
        if isinstance(media, dict) and media.get("schema") is not None:
            entry["schema"] = media["schema"]
        elif resp.get("schema") is not None:  # Swagger 2.0
            entry["schema"] = resp["schema"]
        out[str(status)] = entry
    return out


def parse_openapi(text: str) -> dict[str, Any]:
    """Parse an OpenAPI/Swagger document into contract metadata + endpoint list."""
    doc = _load_document(text)
    info = doc.get("info") or {}

    endpoints: list[dict[str, Any]] = []
    for path, path_item in (doc.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        resolved_item = path_item
        if "$ref" in path_item:
            resolved_item = _resolve_ref(doc, path_item["$ref"]) or {}
        for method in HTTP_METHODS:
            operation = resolved_item.get(method)
            if not isinstance(operation, dict):
                continue
            endpoints.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operation_id": operation.get("operationId"),
                    "summary": operation.get("summary"),
                    "description": operation.get("description"),
                    "tags": [str(t) for t in operation.get("tags") or []],
                    "deprecated": bool(operation.get("deprecated")),
                    "request_schema": _request_schema(doc, operation, resolved_item),
                    "response_schema": _response_schema(doc, operation),
                }
            )

    return {
        "title": info.get("title"),
        "version": str(info.get("version")) if info.get("version") is not None else None,
        "description": info.get("description"),
        "endpoints": endpoints,
    }
