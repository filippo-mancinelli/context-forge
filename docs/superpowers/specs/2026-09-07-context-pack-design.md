# `context_pack` tool — design

## Problem

To start a task an agent calls memory, repo map, repo search, knowledge base
and schema tools one after another, spending five or six round trips and
guessing how much of each to read.

## Design

### Tool

`src/mcp/context_pack.py`, gated with `context-read`:

```
context_pack(
    task: str,
    budget_tokens: int = 6000,          # 1000..32000
    include: list[str] | None = None,   # subset of ["memory", "repo_map", "code", "kb", "db", "web"]
    repos: list[str] | None = None,     # restrict repo sections
) -> dict
```

Response:

```json
{
  "task": "...",
  "budget_tokens": 6000,
  "tokens_used": 5810,
  "sections": [
    {"kind": "memory", "items": [{"id": "...", "text": "...", "score": 0.81, "scope": "project"}], "tokens": 700},
    {"kind": "repo_map", "items": [{"repo": "...", "path": "...", "symbols": ["..."], "score": 0.9}], "tokens": 1700},
    {"kind": "code", "items": [{"repo": "...", "path": "...", "start_line": 10, "end_line": 42, "snippet": "...", "score": 0.77}], "tokens": 1800},
    {"kind": "kb", "items": [{"document": "...", "title": "...", "excerpt": "...", "score": 0.7}], "tokens": 900},
    {"kind": "db", "items": [{"connection": "...", "tables": [{"name": "...", "columns": ["id", "..."]}]}], "tokens": 600},
    {"kind": "web", "items": [], "tokens": 0, "error": null}
  ],
  "omitted": {"code": 4, "kb": 2}
}
```

### Budgeting

- Token estimate: `ceil(len(text) / 4)` (`estimate_tokens` helper; no
  tokenizer dependency).
- Initial shares of the budget: memory 15 %, repo_map 25 %, code 30 %,
  kb 15 %, db 10 %, web 5 %. Sections excluded via `include` give their share
  to the others proportionally. A section that uses less than its share
  releases the remainder to the sections after it, in the order above.
- Items are added in score order until the section share is exhausted; the
  first item of a section is always included even if it exceeds the share,
  truncated to the share with a `"truncated": true` flag. Items not included
  are counted in `omitted`.
- Sources are queried concurrently with `asyncio.gather`; each source has a
  5 s timeout and its own error field. One failing source never fails the pack.

### Sources (reuse, do not reimplement)

The implementer wires each section to the existing internal functions used by
the corresponding tools, bypassing the tool wrappers (permission is checked
once on `context_pack`):

- memory → the search used by `memory_search` (scope `all` when the feature
  exists, else project), limit 8;
- repo_map → the ranking function behind `repo_map` in `src/mcp/code_graph.py`, top 12 files;
- code → `src/search.py` repo search (hybrid), top 10 chunks;
- kb → the search behind `kb_search`, top 6;
- db → the schema overview behind `db_schema` for every connection of the
  project, tables ranked by name/description similarity to the task using the
  existing full-text helpers or a simple token overlap, top 8 tables with
  column names only;
- web → the search behind `web_search`, top 4.

Project scoping is whatever the current MCP context resolves (`use_project`
or path project), identical to the individual tools.

## Tests (no live DB; fake source callables)

- Budget shares with and without `include`; remainder release; first-item
  truncation; omitted counts.
- `estimate_tokens`.
- Source timeout and error isolation (one source raising / hanging).
- Permission gate (`context-read`).
- Argument validation (`budget_tokens` range, unknown `include` value → error).

## README

Tools list: `context_pack` under a new "Task context" line with one sentence
on what it bundles; templates `CLAUDE.md`/`AGENTS.md` suggest calling it first.
