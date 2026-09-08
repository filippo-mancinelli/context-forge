# HNSW vector indexes per organization — design

## Problem

The per-organization embedding migration made the `embedding` columns of
`repo_chunks`, `kb_chunks` and `web_chunks` untyped (`vector` without a
dimension) so each organization can choose its own model. pgvector cannot index
an untyped column, so the old ivfflat indexes were dropped and never recreated:
every vector search is a sequential scan.

## Design

### Index shape

One HNSW index per (table, organization, dimension), partial on the org and
built on a typed cast expression:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS repo_chunks_emb_hnsw_org42_d1536
    ON repo_chunks USING hnsw ((embedding::vector(1536)) vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE org_id = 42;
```

Name pattern: `{table}_emb_hnsw_org{org_id}_d{dims}`. HNSW needs no training
set, so it can be created on an empty table and grows with inserts.

### Query shape

The planner uses an expression index only when the query orders by the same
expression and its `WHERE` implies the partial predicate. `src/search.py`
(repo hybrid and vector SQL) and the kb/web search queries therefore change to:

```sql
ORDER BY (embedding::vector($dims)) <=> $1::vector($dims)
```

with `WHERE org_id = $2` already present. `$dims` is interpolated as an
integer from the organization's effective embedding dimension
(`org_settings` → `embeddings_dims`), never from user input. A helper
`vector_expr(dims: int) -> str` in a new module `src/vector_index.py` builds
the cast text so every query uses one implementation.

### Lifecycle

`src/vector_index.py`:

- `index_name(table, org_id, dims) -> str`
- `async def ensure_org_indexes(org_id: int, dims: int) -> list[str]`: for the
  three tables, `CREATE INDEX CONCURRENTLY IF NOT EXISTS` (autocommit
  connection, `SET maintenance_work_mem = '256MB'` for the session first),
  then drop indexes of the same org with a different dimension
  (`DROP INDEX CONCURRENTLY IF EXISTS`). Returns the names created.
- `async def ensure_all_indexes()`: iterate organizations and call the above.

Triggers:

- at startup, after `ensure_tenant_storage`, as a background task so boot is
  not delayed by a long build;
- at the end of `reembed_org` in `src/reembed.py` (dimension may have changed);
- when the org embedding settings are saved (`src/api/routes/settings.py`)
  only if the dimension is unchanged (a changed dimension already triggers a
  re-embed, which triggers the ensure).

Index build failures are logged with the index name and never fail the caller.

### Search knobs

`SET LOCAL hnsw.ef_search = 100` in the search transaction (recall over speed
for a candidate pool of a few hundred). Constant `HNSW_EF_SEARCH = 100` in
`src/vector_index.py`.

## Tests (no live DB)

- `index_name` and DDL text for a given table/org/dims.
- `ensure_org_indexes` issues the three `CREATE INDEX CONCURRENTLY` statements
  and drops a stale index of the same org with another dimension (fake pool
  returning the stale name from `pg_indexes`).
- `search.py` SQL contains the typed cast on both sides of `<=>` with the org
  dimension, and `ef_search` is set (fake pool captures statements).
- `reembed_org` calls `ensure_org_indexes` once at the end (monkeypatch).

## README

Architecture section: one sentence on HNSW per organization; upgrade note
replaces the "sequential scan" caveat.
