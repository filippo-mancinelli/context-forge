# Retrieval evaluation and reranker — design

## Problem

Organizations can switch embedding models and search modes, but nothing
measures whether retrieval got better. A reranker would be a blind bet.

## Part 1: evaluation harness

### Storage

Migration `0006_eval`:

```sql
CREATE TABLE eval_queries (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL,
    project_id  BIGINT NOT NULL,
    query       TEXT NOT NULL,
    expected    JSONB NOT NULL,   -- [{"kind":"repo","repo":"name","path":"src/a.py"} | {"kind":"kb","document_id":12}]
    notes       TEXT,
    created_by  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE eval_runs (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL,
    project_id  BIGINT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    config      JSONB NOT NULL,   -- {embeddings_model, embeddings_dims, hybrid, reranker_provider, reranker_model, k}
    metrics     JSONB,            -- {recall_at_5, recall_at_10, mrr, queries, hits}
    per_query   JSONB,            -- [{query_id, recall_at_5, recall_at_10, rr, found:[...], missed:[...]}]
    error       TEXT
);
CREATE INDEX eval_runs_project_idx ON eval_runs (project_id, started_at DESC);
```

### Runner

`src/eval/runner.py`: `run_eval(org_id, project_id, k=10) -> run_id`. For each
query: repo expectations go through the repo search (`src/search.py`), kb
expectations through the kb search, both with `limit=k`; an expected item is
found when a result matches repo+path (repo) or document id (kb). Per query:
`recall_at_5`, `recall_at_10` = found within top 5 / 10 divided by the number
of expected items; `rr` = 1 / rank of the first found item (0 if none).
Run metrics are macro averages. The run records the effective config from
the org settings. Runs execute as a background task (`asyncio.create_task`)
and the row is created immediately with `finished_at = NULL`; the UI polls.

### REST (`src/api/routes/eval.py`, org admin/owner; project from `X-Project-Id`)

- `GET /api/eval/queries`, `POST /api/eval/queries`, `PUT /api/eval/queries/{id}`, `DELETE /api/eval/queries/{id}`
- `POST /api/eval/queries/capture` `{query}` → runs repo + kb search and returns
  the top 10 candidates so the UI can tick the correct ones.
- `POST /api/eval/runs` `{k: 10}` → `{run_id}`; `GET /api/eval/runs?limit=20`;
  `GET /api/eval/runs/{id}` (with per-query detail).

### UI

Sidebar page **Retrieval eval** (`pages/RetrievalEval.tsx`, route `/eval`,
admin/owner). Left: golden queries (add form with query text, "Find candidates"
button listing the top 10 results with checkboxes, notes; edit/delete). Right:
runs table (started, model/dims, hybrid, reranker, recall@5, recall@10, MRR,
queries) with "Run now"; selecting a run shows per-query rows with found and
missed items; the two most recent runs show a delta column.

## Part 2: reranker

### Provider abstraction

`src/reranker.py`: `async def rerank(query, candidates: list[dict], text_key,
top_n, org_id) -> list[dict]` returning the candidates reordered with
`rerank_score`. Providers selected by the org setting `reranker_provider`:

- `none` (default): return input unchanged;
- `jina`: POST `https://api.jina.ai/v1/rerank` with model `reranker_model`
  (default `jina-reranker-v2-base-multilingual`), key from `RERANKER_API_KEY`
  falling back to `EMBEDDINGS_API_KEY`, 10 s timeout;
- `local`: `sentence_transformers.CrossEncoder(reranker_model)` (default
  `cross-encoder/ms-marco-MiniLM-L-6-v2`), imported lazily, only when the
  package is installed (the local embeddings build), else a clear error at
  settings save time.

Any provider error logs a warning and returns the fused order (fail open).

Settings (`Settings`, per-org overrides, Settings UI "Search" block):
`reranker_provider` (none | jina | local), `reranker_model`,
`reranker_top_n` (default 30, the number of fused candidates sent to the
reranker); `RERANKER_API_KEY` env only.

### Integration

`src/search.py` repo search and the kb search: after RRF fusion (or the
vector ranking when hybrid is off), take the top `reranker_top_n` candidates,
call `rerank`, then return the top `limit`. The eval run config records the
reranker so runs before and after show the effect.

## Tests (no live DB)

- Metric maths on fixed found/missed fixtures (recall@k, MRR, macro average).
- Runner with fake search functions and fake pool: run row created, metrics
  and per-query stored, error captured.
- Capture endpoint shape; CRUD role gating; project scoping.
- Reranker: `none` passthrough; `jina` request payload and response parsing
  (httpx mocked); fail-open on error; `local` import guard.
- Search integration: reranker called with `reranker_top_n` candidates and the
  final order follows `rerank_score` (fake reranker).

## README

Features: evaluation and reranking bullets; Settings/env vars documented;
tools list unchanged.
