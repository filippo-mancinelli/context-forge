# Human approvals for write tools — design

## Problem

`db_execute` and `ssh_write_file` need `db-write` / `ssh-write`, owner-only by
default. Members cannot use them at all, and owners run them unreviewed. The
PR flow (`repo_open_pr`) already follows "the agent proposes, a human
disposes"; SQL and file writes should too.

## Design

### Behaviour

- A caller **with** the write permission executes immediately, as today.
- A caller **without** it but with `context-write` gets a proposal instead:
  the tool stores a write request with a preview and returns
  `{"status": "pending_approval", "request_id": 12, "preview": {...},
    "message": "An organization admin must approve this change. Poll with write_request_status(12)."}`.
- New tool `write_request_status(request_id)` → status, decision note, result
  when executed. Gated with `context-read`; scoped to the caller's org and project.
- Callers with neither permission are denied as today.

### Preview

- `db_execute`: run `EXPLAIN (FORMAT JSON) <sql>` on the target connection
  inside a transaction that is rolled back (through the existing read-only
  executor path with the statement timeout); store `plan_rows` (top node
  "Plan Rows"), `plan_text` (first 2000 chars of the JSON), `statement`.
  Validation of the statement (`src/datasources/validator.py`) runs before the
  preview exactly as for a direct execute.
- `ssh_write_file`: store `path`, `content` (max 512 KiB, else refused),
  `content_sha256`, `existing_sha256` and `existing_size` when the file
  exists (read through the SSH client), `is_new`.

### Storage

Migration `0004_write_requests`:

```sql
CREATE TABLE write_requests (
    id             BIGSERIAL PRIMARY KEY,
    org_id         BIGINT NOT NULL,
    project_id     BIGINT NOT NULL,
    kind           TEXT NOT NULL,      -- db_execute | ssh_write_file
    status         TEXT NOT NULL DEFAULT 'pending',
                   -- pending | approved | rejected | executed | failed | expired
    target         TEXT NOT NULL,      -- connection name or "<source>:<path>"
    payload        JSONB NOT NULL,     -- {sql} | {path, content}
    preview        JSONB NOT NULL,
    reason         TEXT,               -- agent justification (tool argument)
    requested_by_kind TEXT NOT NULL, requested_by_id BIGINT, requested_by TEXT NOT NULL,
    decided_by     BIGINT, decided_at TIMESTAMPTZ, decision_note TEXT,
    result         JSONB, error TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at     TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '7 days'
);
CREATE INDEX write_requests_org_status_idx ON write_requests (org_id, status, created_at DESC);
```

Both tools gain an optional `reason: str = ""` argument stored on the request.

### Approval

Service `src/write_requests.py`: `create`, `get`, `list_for_org`, `approve`,
`reject`, `expire_pending`. `approve(request_id, user_id)`:

1. the approver must hold the write permission the request needs, resolved
   from their org role through `resolve_role_permissions` (owner always);
2. status must be `pending` and not expired;
3. set `approved`, then execute with the existing executors
   (`datasources.service.run_write` / the SSH write function), then `executed`
   with `result`, or `failed` with `error`. Execution runs under the approver's
   identity (audit principal = approver).

Scheduler daily: `expire_pending` marks overdue pending requests `expired`.

### REST

`src/api/routes/write_requests.py`, org admin/owner (approve additionally
requires the write permission as above):

- `GET /api/write-requests?status=pending&limit=50&offset=0` (current org, all
  projects the caller can see) → `{"requests": [...], "total": n, "pending": n}`
- `GET /api/write-requests/{id}`
- `POST /api/write-requests/{id}/approve` `{ "note": "" }`
- `POST /api/write-requests/{id}/reject` `{ "note": "" }`

### UI

New sidebar page **Approvals** (`pages/Approvals.tsx`, route `/approvals`),
visible to admin/owner; nav badge with the pending count (fetched with the
project list on load and after each decision). Page: filter by status
(default pending), list with kind icon, target, requester, age, expiry; detail
drawer showing the SQL or the file diff summary (path, new/modified, sizes,
hashes) and the preview (estimated rows, plan excerpt); Approve / Reject with
a note; executed requests show the result rows or bytes written.

## Tests (no live DB)

- Tool dispatch: with write permission → direct execution path called; with
  `context-write` only → request created, preview captured, response shape;
  with neither → denied.
- Preview builders for SQL (EXPLAIN parsed) and file (hashes, size limit).
- `approve` permission check, state machine (pending → approved → executed /
  failed; rejected; expired), and execution with fake executors.
- `write_request_status` scoping (another org/project → not found).
- REST role gating and pending count.
- UI: `tsc`, build; badge count logic unit-tested if extracted to `lib/`.

## README

Features bullet for data sources and SSH mentions proposals; MCP tools list
gains `write_request_status`; Security section explains the approval flow and
that approvals require the write permission.
