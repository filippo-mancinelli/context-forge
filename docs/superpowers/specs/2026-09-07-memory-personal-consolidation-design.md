# Personal memory and consolidation — design

## Problem

Memory has one namespace per project (Mem0 `user_id` = project namespace).
There is no personal space, no record of which memories are actually used,
and nothing removes duplicates or lets stale memories fade.

## Design

### Scopes

- `project` scope: today's namespace (`resolve_memory_namespace()`).
- `personal` scope: namespace `f"{project_namespace}::u{user_id}"` where
  `user_id` is the authenticated user. For API keys, the key's creator
  (`mcp_api_keys.created_by`, verified by the implementer; if the column does
  not exist, add it in the migration and populate it on key creation) is the
  user. Anonymous callers cannot use the personal scope: the tool returns
  `{"status": "error", "error": "Personal memory requires an authenticated caller"}`.

Tool signatures (`src/mcp/memory.py`):

- `memory_add(content, metadata=None, infer=True, scope="project")`
- `memory_search(query, limit=10, scope="all", include_stale=False)`:
  `all` searches both namespaces, merges by score, returns `scope` per item.
- `memory_list(limit=20, scope="all", include_stale=False)`
- `memory_delete(memory_id)` unchanged (ownership check covers both namespaces).

REST memory routes (`src/api/routes/memory.py`) accept the same `scope` and
`include_stale` query/body fields; the UI Memory page gets a scope selector
(Project / Mine / All) on the list and on the add form, and a "stale" badge with
Restore and Delete actions.

### Usage tracking

Migration `0005_memory_stats`:

```sql
CREATE TABLE memory_stats (
    memory_id   TEXT PRIMARY KEY,
    namespace   TEXT NOT NULL,
    org_id      BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_hit_at TIMESTAMPTZ,
    hits        INT NOT NULL DEFAULT 0,
    stale_at    TIMESTAMPTZ,
    content_hash TEXT NOT NULL
);
CREATE INDEX memory_stats_ns_idx ON memory_stats (namespace);
```

`memory_add` inserts the row (content hash = sha256 of the normalised text:
lowercase, collapsed whitespace, stripped punctuation). `memory_search` and
the REST search bump `hits` and `last_hit_at` for every returned id in one
`UPDATE ... WHERE memory_id = ANY($1)`. Stale memories are filtered out of
search and list results unless `include_stale` (post-filter; fetch
`limit * 2` from Mem0 to compensate). `memory_delete` removes the row.

### Consolidation job

`src/memory_consolidation.py`, `consolidate_namespace(org_id, namespace) -> dict`:

1. **Dedup**: group memories by `content_hash`; for groups larger than one
   keep the oldest, add the others' hits to it, delete the others from Mem0 and
   `memory_stats`. Near-duplicates: token Jaccard ≥ 0.9 between normalised
   texts within the namespace (O(n²) capped at 2000 memories per namespace;
   above the cap only exact dedup runs and a warning is logged).
2. **Decay**: memories older than `MEMORY_DECAY_DAYS` (default 180) with no hit
   in the last `MEMORY_DECAY_IDLE_DAYS` (default 90) get `stale_at = NOW()`.
   Never deleted automatically.
3. Returns `{"deduplicated": n, "marked_stale": m, "scanned": k}`.

`consolidate_all()` iterates every project namespace and personal namespace
known to `memory_stats`. Scheduler: daily. Manual trigger:
`POST /api/memory/consolidate` (org admin/owner) runs the current project's
namespaces and returns the counts; a button on the Memory page calls it.

Settings: `MEMORY_DECAY_DAYS`, `MEMORY_DECAY_IDLE_DAYS` in `Settings`,
`.env.example`, compose.

Memories created before this feature have no `memory_stats` row: the
consolidation job backfills rows for ids returned by `memory.get_all` that are
missing (created_at = Mem0's `created_at` when present, else now).

## Tests (no live DB, fake Mem0 client as in the existing memory tests)

- Namespace derivation for personal scope; anonymous rejection.
- `memory_search(scope="all")` merges and labels results; stale filtered
  unless `include_stale`; hit bump SQL issued with the returned ids.
- Consolidation: exact dedup keeps oldest and merges hits; Jaccard
  near-duplicate detection threshold; decay marks only old-and-idle; cap
  behaviour; backfill of missing stats rows.
- REST scope parameters and admin gating of the consolidate endpoint.

## README

Memory feature bullet: personal scope and consolidation; tools list shows
the `scope` argument; env vars documented.
