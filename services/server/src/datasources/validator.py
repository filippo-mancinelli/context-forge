"""SQL safety validator for read-only execution against external databases.

Ported and generalized from the askmechat-sql-agent validator: allows a single
SELECT / WITH / SHOW / DESCRIBE / EXPLAIN statement, blocks every mutating or
session-altering keyword, and injects a LIMIT when missing. Validation is
deliberately conservative: a blocked keyword inside a string literal rejects
the query rather than risking a false negative.
"""
from __future__ import annotations

import re

_BLOCKED_KEYWORDS = [
    "DROP",
    "DELETE",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "INSERT",
    "UPDATE",
    "REPLACE",
    "RENAME",
    "GRANT",
    "REVOKE",
    "LOCK",
    "UNLOCK",
    "CALL",
    "LOAD",
    "EXEC",
    "EXECUTE",
    "MERGE",
    "COPY",
    "VACUUM",
    "ANALYZE",
    "REINDEX",
    "CLUSTER",
    "PRAGMA",
    "ATTACH",
    "DETACH",
    "PREPARE",
    "DEALLOCATE",
    "HANDLER",
    "DO",
    "LISTEN",
    "NOTIFY",
    "REFRESH",
    "INTO OUTFILE",
    "INTO DUMPFILE",
]

_BLOCKED_PATTERN = re.compile(
    r"(?:^|[\s;(])(" + "|".join(re.escape(k) for k in _BLOCKED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
# `SET var = ...` needs its own pattern: plain \bSET\b would reject ORDER BY ... OFFSET
# and column names like "settings"; we only care about statement-leading SET.
_SET_PATTERN = re.compile(r"(?:^|;)\s*SET\b", re.IGNORECASE)

_LIMIT_PATTERN = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_SELECT_PATTERN = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_INFO_PATTERN = re.compile(r"^\s*(SHOW|DESCRIBE|DESC|EXPLAIN)\b", re.IGNORECASE)
_COMMENT_PATTERN = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.DOTALL)


class QueryValidationError(Exception):
    """Raised when a SQL query fails read-only validation."""


def validate_query(sql: str, max_rows: int = 100) -> str:
    """Validate a query for safe read-only execution and return the sanitized SQL.

    Raises QueryValidationError for anything that is not a single read-only
    statement. SELECT/WITH statements without a LIMIT get ``LIMIT max_rows``
    appended.
    """
    if not sql or not sql.strip():
        raise QueryValidationError("Empty query.")

    # Strip comments so keywords can't be smuggled past (or hidden by) them.
    cleaned = _COMMENT_PATTERN.sub(" ", sql).strip().rstrip(";").strip()
    if not cleaned:
        raise QueryValidationError("Empty query.")

    if ";" in cleaned:
        raise QueryValidationError("Multiple statements are not allowed.")

    match = _BLOCKED_PATTERN.search(cleaned)
    if match:
        raise QueryValidationError(
            f"Query contains blocked operation: {match.group(1).upper()}. "
            "Only read-only statements are allowed."
        )
    if _SET_PATTERN.search(cleaned):
        raise QueryValidationError(
            "SET statements are not allowed. Only read-only statements are allowed."
        )

    is_select = bool(_SELECT_PATTERN.match(cleaned))
    is_info = bool(_INFO_PATTERN.match(cleaned))
    if not is_select and not is_info:
        raise QueryValidationError(
            "Only SELECT, WITH...SELECT, SHOW, DESCRIBE, and EXPLAIN statements are allowed."
        )

    if is_select and not _LIMIT_PATTERN.search(cleaned):
        cleaned = f"{cleaned} LIMIT {max_rows}"

    return cleaned


def is_safe_query(sql: str) -> bool:
    try:
        validate_query(sql)
        return True
    except QueryValidationError:
        return False
