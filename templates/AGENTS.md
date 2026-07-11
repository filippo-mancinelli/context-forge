# Agent Instructions — context-forge

You have access to **context-forge**, a persistent context platform with the following MCP tools.

## Memory

Use these tools to store and retrieve persistent information across sessions:

- **`memory_add(content, metadata?)`** — Save important facts, decisions, architectural notes, or any information that should be remembered in future sessions. Call this proactively whenever you learn something significant.
- **`memory_search(query, limit?)`** — Search stored memories semantically before starting a task. Always search memory first when working on a project you may have worked on before.
- **`memory_list(limit?)`** — List recent memories.
- **`memory_delete(memory_id)`** — Delete a specific memory.

**When to use memory:**
- After making an architectural decision → `memory_add("We use PostgreSQL + pgvector for all vector storage")`
- After learning a convention → `memory_add("API routes follow REST: /api/v1/resource")`
- After fixing a tricky bug → `memory_add("Fixed auth bug: tokens expire after 1h, always refresh on 401")`
- At the start of a session → `memory_search("project conventions")` to restore context

## Repositories

Use these tools to navigate and search across indexed codebases:

- **`repo_search(query, repos?, limit?)`** — Semantic search across all indexed repositories. Use this to find relevant code, understand patterns, or discover where things are implemented.
- **`repo_get_file(repo, path)`** — Read a specific file by repo name and path.
- **`repo_list()`** — List all indexed repos with their status.
- **`repo_index(repo?)`** — Trigger re-indexing (after major changes).
- **`repo_relationships(repo?)`** — Discover semantically related repositories.
- **`repo_references(symbol, repo?, limit?)`** — Find all references to a function, class, or variable across indexed repos. Returns each usage with file path, line numbers, and content.
- **`code_explain(repo, file_path, start_line?, end_line?)`** — Get an AI explanation of what a piece of code does. Reads code from the repo and sends it to the configured LLM.
- **`repo_annotate(repo, file_path, note, start_line?, end_line?)`** — Add a persistent note/annotation on a file or code chunk visible to the whole organization.
- **`repo_annotations(repo, file_path?, limit?)`** — Read existing annotations on a repo's files.

**When to use repo tools:**
- Before modifying a module → `repo_search("authentication middleware")` to find related code
- When exploring a new codebase → `repo_relationships()` to understand what repos connect to what
- When looking for examples → `repo_search("database connection pooling example")`

## Async Jobs

Use these tools for long-running HTTP calls that might otherwise time out:

- **`job_submit(url, method?, payload?, headers?)`** — Submit an HTTP request as a background job. Returns `job_id` immediately.
- **`job_status(job_id)`** — Check if job is pending/running/done/error.
- **`job_result(job_id)`** — Retrieve the result once done.

**Pattern:**
```
job = job_submit(url="http://...", payload={...})
# wait a moment, then poll:
status = job_status(job["job_id"])
# when status is "done":
result = job_result(job["job_id"])
```

## Data Sources (external databases)

Use these tools to explore configured database connections and run read-only SQL:

- **`db_list()`** — List all database connections (name, engine, database, description). **Always call this first** when you need a database but don't know the exact connection name.
- **`db_schema(connection?, hint?, schema?)`** — Schema overview (tables, views, row estimates). Omit `connection` to list connections. Use `hint` when inferring from conversation (e.g. user discussed "context-forge" → `hint="context-forge"`).
- **`db_describe(table, connection?, hint?, schema?)`** — Full table detail: columns, keys, indexes, curated descriptions.
- **`db_query(sql, connection?, hint?)`** — Run a single read-only SELECT/SHOW/EXPLAIN query.

**Workflow:**
```
db_list()                                    # see what's available
db_schema(hint="context-forge")              # infer connection from project name
db_describe("users", hint="context-forge")     # inspect a table
db_query("SELECT count(*) FROM users", hint="context-forge")
```

**Important:** Connection names often match project/repo names. Never pass placeholders like `__list__` — use `db_list()` or omit `connection`.

## Best Practices

1. **Always search memory first** when starting work on a project you've touched before.
2. **Save decisions proactively** — don't wait to be asked. If you discover something useful, save it.
3. **Use repo_search over grep** for conceptual searches ("how is X implemented?"). Use grep/file reading for exact string searches.
4. **Use job_submit** for any HTTP call to external services that might take > 10 seconds.
