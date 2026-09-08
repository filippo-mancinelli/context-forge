# Operational robustness — design

Three independent parts: durable job retries, Prometheus metrics, a detailed
health endpoint.

## 1. Job retry with backoff and dead letter

Today `job_submit` fires a background HTTP task and forgets it: a restart loses
running jobs and a transient 5xx is a permanent error.

Migration `0002_jobs_retry` adds to `jobs`:
`attempts INT NOT NULL DEFAULT 0`, `max_attempts INT NOT NULL DEFAULT 3`,
`next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `last_error TEXT`.
Status values: `pending | running | done | error | dead`.

Execution moves to the scheduler: `_check_jobs` every 5 s claims up to 5
`pending` jobs with `next_attempt_at <= NOW()` using
`UPDATE ... SET status='running', attempts = attempts + 1 ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING *`
and runs them concurrently. `job_submit` only inserts (immediate ack, unchanged
response). Jobs stuck in `running` for more than 10 minutes (crash mid-run) are
reset to `pending` by the same check.

Outcome rules for HTTP jobs (`src/mcp/jobs.py`):

- 2xx → `done`;
- 4xx (except 408, 429) → `error`, no retry;
- 5xx, 408, 429, timeout, connection error → retry if `attempts < max_attempts`
  with backoff `5s * 4^(attempts-1)` capped at 5 minutes, else `dead`.

`job_status` reports `attempts`, `max_attempts`, `next_attempt_at` and the
`dead` state; `job_submit` accepts optional `max_attempts` (1–10).

## 2. Prometheus metrics

Dependency `prometheus-client>=0.20`. Endpoint `GET /metrics` on the API app,
text exposition format. Optional `METRICS_TOKEN` env (in `Settings`,
`.env.example`, compose): when set, requests need `Authorization: Bearer <token>`;
when empty the endpoint is open (intended for private scraping). Labels never
contain org ids.

Metrics (registry in new module `src/metrics.py`):

- `contextforge_jobs{status}` gauge, refreshed by a scheduler tick every 30 s;
- `contextforge_index_requests_pending` gauge and
  `contextforge_index_requests_oldest_age_seconds` gauge;
- `contextforge_kb_documents_pending`, `contextforge_web_pages_pending` gauges;
- `contextforge_scheduler_tick_timestamp_seconds` gauge (heartbeat);
- `contextforge_schema_version` gauge;
- `contextforge_mcp_tool_calls_total{tool,outcome}` counter, declared here,
  incremented by the audit feature later (declaring it now keeps one registry).

## 3. Detailed health

`GET /api/health` stays as is (public, minimal). New `GET /api/health/details`
for authenticated org admins/owners:

```json
{
  "status": "ok" | "degraded",
  "database": {"ok": true, "latency_ms": 3},
  "schema_version": 2,
  "scheduler": {"running": true, "last_tick_at": "..."},
  "queues": {
    "jobs_pending": 0, "jobs_running": 0, "jobs_dead": 0,
    "index_requests_pending": 0, "index_requests_oldest_age_seconds": 0,
    "kb_documents_pending": 0, "web_pages_pending": 0
  }
}
```

`degraded` when the database check fails, the scheduler has not ticked for
more than 60 s, or `index_requests_oldest_age_seconds > 3600`.

## Tests (no live DB)

- Backoff function values for attempts 1..5 and the cap.
- Outcome classification for status codes and exceptions.
- `_check_jobs` claims, runs and finalises with a fake pool (status
  transitions, attempts increment, dead letter after max attempts).
- Stuck-running reset.
- `/metrics` returns the declared metric names; token enforcement when
  `METRICS_TOKEN` is set.
- `/api/health/details` shape and `degraded` rules with fake queue readings.

## README and config

`.env.example` and compose: `METRICS_TOKEN=`. README: metrics and health
details paragraph under Architecture; jobs bullet mentions retries and dead
letter.
