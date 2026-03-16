# context-forge — Claude Code Instructions

You are connected to **context-forge** via MCP. Use the following tools throughout every session.

## Mandatory Behaviors

**At the start of every session:**
```
memory_search("project conventions and recent decisions")
repo_list()
```

**When completing significant work:**
```
memory_add("Implemented X using Y approach because Z")
```

## Available Tools

### Memory (persistent across all sessions)
| Tool | When to use |
|------|-------------|
| `memory_add(content)` | After every significant decision, discovery, or fix |
| `memory_search(query)` | Before starting any task on a known project |
| `memory_list()` | To review what's been stored |

### Repository Search
| Tool | When to use |
|------|-------------|
| `repo_search(query)` | Finding where patterns/features are implemented |
| `repo_get_file(repo, path)` | Reading specific files |
| `repo_relationships()` | Understanding cross-repo dependencies |

### Async Jobs (for slow services)
| Tool | When to use |
|------|-------------|
| `job_submit(url, payload)` | Calling services that take > 10s |
| `job_status(job_id)` | Polling for completion |
| `job_result(job_id)` | Getting the result |

## Memory Examples

```python
# Store architecture decisions
memory_add("Database: PostgreSQL with pgvector. ORM: SQLAlchemy async. Migrations: Alembic.")

# Store project conventions  
memory_add("All API endpoints return {status, data, error}. Auth via Bearer token in header.")

# Store environment info
memory_add("Dev: docker compose up. Prod: dokploy on vps.mycompany.com. DB: separate postgres container.")

# Store bug fixes
memory_add("BUG FIX: asyncpg pool exhaustion in tests — use separate pool per test with @pytest.fixture(scope='function')")
```
