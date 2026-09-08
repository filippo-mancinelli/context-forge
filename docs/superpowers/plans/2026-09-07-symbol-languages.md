# More Languages in the Symbol Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend parsing and the symbol graph from Python/JS/TS/Go/Java to C#, PHP, Kotlin, Rust (tree-sitter grammars) and SQL (regex extractor), so `repo_map`, `repo_neighbors` and `repo_symbols` work on those repositories too.

**Architecture:** `src/indexer/indexer.py` gains a per-language tree-sitter loader table with a process-level parser cache (today one `try` block imports every grammar, so a single missing wheel silences all of them, and the parser is rebuilt for every file). `src/indexer/symbols.py` gains three small override tables — a name-field override, a name-holder override, and extra reference node types — so each new grammar contributes one row instead of a special case in the walker. SQL has no maintained grammar, so it gets `extract_sql_symbols(content)`, a regex extractor for `CREATE …` statements, dispatched from `extract_symbols` before the parser is required.

**Tech Stack:** Python 3.11 (local dev 3.14), tree-sitter >= 0.23 with the `tree-sitter-c-sharp`, `tree-sitter-php`, `tree-sitter-kotlin`, `tree-sitter-rust` grammar wheels, pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-symbol-languages-design.md`

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-09-07-platform-improvements-roadmap.md` ("Global constraints (bind every plan)"):

- Server: Python 3.11 in Docker, local dev on 3.14; code root `services/server/src`
  (import root `src.*`), tests in `services/server/tests`, run with
  `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` from `services/server`.
  Tests never need a live database: follow the fake-pool pattern already used
  by the existing tests (search `tests/` for `get_pool` monkeypatching).
- All DB access is raw SQL through `src.db.get_pool()` (asyncpg). No ORM.
- Schema changes: from feature 1 on, only as modules in `src/migrations/versions/`.
- MCP tools live in `src/mcp/*.py`, are imported in `src/main.py`, and are gated
  with `@requires_permission("<permission>")` from `src/mcp/permissions.py`.
  New permissions are added to `PERMISSIONS` there and documented in README.
- Per-organization settings go through `src/org_settings.py`; new keys need a
  default and a UI field in Settings when user-facing.
- Scheduler jobs are registered in `src/scheduler.py` (`start_scheduler`).
- REST routes: `src/api/routes/<area>.py`, included in `src/api/app.py`,
  role checks via the helpers in `src/api/deps.py` / `src/tenancy.py`.
- UI: React 18 + TypeScript in `services/ui/src`; API client in `lib/api.ts`,
  store in `store/index.ts`, kit in `components/ui`, pages in `pages/`, routes
  and nav in `App.tsx`. Role gating follows `pages/Organization.tsx`. Verify
  with `npx tsc --noEmit`, `npm run build`, `npx vitest run` from `services/ui`.
  UI copy is English.
- New Python dependencies go in `services/server/pyproject.toml` and are
  installed in the local venv with `uv pip install --python .venv/Scripts/python.exe -e .`
  (run from `services/server`). No new system packages unless the Dockerfile
  is updated in the same task.
- Naming: the product is `context-forge` / `ContextForge`. Never `askme`.
- Code comments: max one line, only where needed. Commit titles 3–10 words,
  English, imperative; body optional, max 20 words.
- README: each feature updates the sections it touches (features, tools,
  env vars, permissions) in the same plan.
- Do not touch `services/server/src/api/routes/product_*`: it does not exist
  here and must not be recreated.

Feature-specific constraints:

- This feature adds **no** schema change, **no** migration module, **no** MCP tool, **no** MCP permission, **no** org setting and **no** UI change. Files under `services/ui` are not touched.
- This feature adds **no** system package: all four grammars publish `abi3` wheels for manylinux x86_64/aarch64 and win_amd64, so `services/server/Dockerfile` is **not** modified.
- Every command below runs from `services/server` unless stated otherwise. Test command: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/<file>`. Dependency install: `uv pip install --python .venv/Scripts/python.exe -e .`.
- Code comments in `src/indexer/*.py` follow the file's existing language (Italian in `symbols.py` and in the symbol-graph parts of `indexer.py`, English in the older indexer helpers). Match the surrounding block.
- Verified grammar pins (checked on PyPI, `abi3` wheels present for cp311 on manylinux x86_64, manylinux aarch64 and win_amd64, all loading under `tree-sitter` 0.26 with language ABI 14/15):

  | Language key | PyPI package | Pin | Import module | Language function |
  |---|---|---|---|---|
  | `csharp` | `tree-sitter-c-sharp` | `>=0.23.5` | `tree_sitter_c_sharp` | `language()` |
  | `php` | `tree-sitter-php` | `>=0.24.1` | `tree_sitter_php` | `language_php()` |
  | `kotlin` | `tree-sitter-kotlin` | `>=1.1.0` | `tree_sitter_kotlin` | `language()` |
  | `rust` | `tree-sitter-rust` | `>=0.24.2` | `tree_sitter_rust` | `language()` |

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `src/indexer/indexer.py` | extension sets, extension → language map, per-language parser loading + cache, chunk node table and depth limit | 1, 2, 3, 4, 5, 6 |
| `src/indexer/symbols.py` | definition node table, name-resolution overrides, reference node overrides, SQL regex extractor | 1, 2, 3, 4, 5, 6 |
| `pyproject.toml` | grammar dependencies | 2, 3, 4, 5 |
| `tests/test_language_wiring.py` | detection, parser loading/cache, chunk-table fallback (new) | 1 |
| `tests/test_symbols_csharp.py` | C# extraction + chunking (new) | 2 |
| `tests/test_symbols_php.py` | PHP extraction (new) | 3 |
| `tests/test_symbols_kotlin.py` | Kotlin extraction (new) | 4 |
| `tests/test_symbols_rust.py` | Rust extraction (new) | 5 |
| `tests/test_symbols_sql.py` | SQL regex extraction (new) | 6 |
| `tests/test_symbol_rows_persist.py` | `.cs` file → symbol rows through the persistence path, fake pool (new) | 7 |
| `README.md` (repo root) | architecture sentence + code-intelligence bullet | 7 |

---

### Task 1: Extension and detection wiring, per-language parser loading

Nothing language-specific is added here: this task builds the four hooks every later task plugs into — the extension → language map (all new extensions at once), a per-language grammar loader with a parser cache, name-resolution overrides in the symbol extractor, and a chunk node table that falls back to `DEF_NODES`. After this task `.cs`, `.php`, `.kt`, `.kts`, `.rs` files become indexable (chunked with the sliding window, since no grammar is installed yet) and `.sql` files are detected as language `sql`.

**Files:**
- Modify: `services/server/src/indexer/indexer.py`
- Modify: `services/server/src/indexer/symbols.py`
- Test: `services/server/tests/test_language_wiring.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `src.indexer.indexer._LANGUAGE_LOADERS: dict[str, tuple[str, str]]` — language key → `(module_name, language_function_name)`. Later tasks add one row each.
  - `src.indexer.indexer._PARSER_CACHE: dict[str, Any]` — language key → parser or `None`.
  - `src.indexer.indexer._get_parser(language: str)` — unchanged signature, now cached and per-grammar isolated.
  - `src.indexer.indexer._EXT_LANGUAGES: dict[str, str]` — module-level extension → language map used by `_detect_language(path: Path) -> str`.
  - `src.indexer.indexer._CHUNK_NODES: dict[str, list[str]]` — module-level chunk node table (was a local variable).
  - `src.indexer.indexer._CHUNK_MAX_DEPTH: dict[str, int]` — language key → max nesting depth for chunking, default 2.
  - `src.indexer.symbols.NAME_FIELDS: dict[tuple[str, str], str]` — `(language, node_type)` → field name holding the declared name. Default is `"name"`.
  - `src.indexer.symbols.NAME_HOLDERS: dict[tuple[str, str], str]` — `(language, node_type)` → type of the child node that holds the name.
  - `src.indexer.symbols.REF_NODES_EXTRA: dict[str, set[str]]` — language key → extra reference node types.
  - `src.indexer.symbols._name_node(node, language: str = "")` — returns the tree-sitter node carrying the declared name, or `None`.
  - `src.indexer.symbols._name_of(node, source: bytes, language: str = "")` — the declared name as `str`, or `None`.
  - `src.indexer.symbols._ref_nodes(language: str) -> set[str]`.

**Context you need (current code in `services/server/src/indexer/indexer.py`):**

```python
PARSEABLE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java"}
TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".yaml", ".yml", ".toml", ".json", ".env",
    ".sh", ".bash", ".sql", ".css", ".html", ".xml", ".ini", ".cfg",
    ".dockerfile", ".gitignore", ".proto",
}
```

`_is_indexable()` ends with `return suffix in PARSEABLE_EXTENSIONS or suffix in TEXT_EXTENSIONS`, and `_chunk_file()` runs `_extract_chunks_treesitter` only when `suffix in PARSEABLE_EXTENSIONS`, falling back to `_sliding_window_chunks` when that returns `[]`. `.sql` **stays** in `TEXT_EXTENSIONS`: that is already the "chunk as text" path the spec asks for.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_language_wiring.py`:

```python
"""Rilevamento del linguaggio, caricamento delle grammatiche e tabella di chunking.

Nessuna grammatica nuova serve qui: si verificano gli agganci che i linguaggi
aggiunti dopo useranno.
"""
from pathlib import Path

from src.config import IndexingConfig
from src.indexer import indexer, symbols

JAVA_SRC = """public class AssignmentSchedulerService {
    public void processStandardCandidates(String target) {
        OperatorLoadState state = loadOperatorState(target);
        state.availableSlots(maxActiveTicketsPerOperator);
    }
}
"""

PY_SRC = b"class AssignmentScheduler:\n    pass\n"


def test_new_extensions_map_to_their_language():
    assert indexer._detect_language(Path("Scheduler.cs")) == "csharp"
    assert indexer._detect_language(Path("Scheduler.php")) == "php"
    assert indexer._detect_language(Path("Scheduler.kt")) == "kotlin"
    assert indexer._detect_language(Path("build.gradle.kts")) == "kotlin"
    assert indexer._detect_language(Path("scheduler.rs")) == "rust"
    assert indexer._detect_language(Path("schema.sql")) == "sql"
    assert indexer._detect_language(Path("notes.md")) == "text"


def test_existing_extensions_are_unchanged():
    assert indexer._detect_language(Path("a.py")) == "python"
    assert indexer._detect_language(Path("a.jsx")) == "javascript"
    assert indexer._detect_language(Path("a.tsx")) == "tsx"
    assert indexer._detect_language(Path("A.java")) == "java"


def test_source_extensions_are_parseable_and_sql_stays_text():
    for ext in (".cs", ".php", ".kt", ".kts", ".rs"):
        assert ext in indexer.PARSEABLE_EXTENSIONS
    assert ".sql" in indexer.TEXT_EXTENSIONS
    assert ".sql" not in indexer.PARSEABLE_EXTENSIONS


def test_parser_is_built_once_per_language():
    first = indexer._get_parser("python")
    assert first is not None
    assert indexer._get_parser("python") is first


def test_unknown_language_has_no_parser():
    assert indexer._get_parser("cobol") is None


def test_a_missing_grammar_does_not_disable_the_others(monkeypatch):
    monkeypatch.setattr(indexer, "_PARSER_CACHE", {})
    monkeypatch.setitem(indexer._LANGUAGE_LOADERS, "klingon", ("tree_sitter_klingon", "language"))
    assert indexer._get_parser("klingon") is None
    assert indexer._get_parser("java") is not None


def test_chunk_targets_fall_back_to_the_definition_nodes(monkeypatch):
    monkeypatch.delitem(indexer._CHUNK_NODES, "java")
    chunks = indexer._extract_chunks_treesitter(JAVA_SRC, "java", IndexingConfig())
    assert [c["type"] for c in chunks] == ["class_declaration"]
    assert chunks[0]["name"] == "AssignmentSchedulerService"


def test_the_chunk_depth_limit_is_per_language(monkeypatch):
    monkeypatch.setitem(indexer._CHUNK_MAX_DEPTH, "java", 0)
    assert indexer._extract_chunks_treesitter(JAVA_SRC, "java", IndexingConfig()) == []


def test_name_resolution_defaults_to_the_name_field():
    parser = indexer._get_parser("python")
    node = parser.parse(PY_SRC).root_node.children[0]
    assert node.type == "class_definition"
    assert symbols._name_of(node, PY_SRC, "python") == "AssignmentScheduler"


def test_reference_nodes_can_be_extended_per_language(monkeypatch):
    assert symbols._ref_nodes("python") == symbols.REF_NODES
    monkeypatch.setitem(symbols.REF_NODES_EXTRA, "klingon", {"qapla"})
    assert symbols._ref_nodes("klingon") == symbols.REF_NODES | {"qapla"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_language_wiring.py
```

Expected: FAIL — `AttributeError: module 'src.indexer.indexer' has no attribute '_LANGUAGE_LOADERS'`, plus assertion failures on `_detect_language(Path("Scheduler.cs")) == "csharp"`.

- [ ] **Step 3: Rewrite parser loading and detection in `src/indexer/indexer.py`**

Add `import importlib` to the stdlib import block at the top of the file (it already imports `asyncio, fnmatch, hashlib, json, logging, os, re, time`), and add `from .symbols import DEF_NODES` next to the existing `from .embedder import embed_batch`. There is no import cycle: `symbols.py` imports only the standard library.

Extend `PARSEABLE_EXTENSIONS` and leave `TEXT_EXTENSIONS` / `BINARY_EXTENSIONS` untouched:

```python
PARSEABLE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java",
    ".cs", ".php", ".kt", ".kts", ".rs",
}
```

Replace the whole `_get_parser` function (it currently imports all six grammars inside a single `try`, so one missing wheel silences every language, and it rebuilds the parser on every call) with:

```python
# Grammatiche tree-sitter: linguaggio -> (modulo, funzione che ritorna la lingua).
_LANGUAGE_LOADERS: dict[str, tuple[str, str]] = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "java": ("tree_sitter_java", "language"),
}

_PARSER_CACHE: dict[str, Any] = {}


def _get_parser(language: str):
    """Get a tree-sitter parser for the given language. Returns None if unsupported."""
    if language in _PARSER_CACHE:
        return _PARSER_CACHE[language]
    parser = None
    entry = _LANGUAGE_LOADERS.get(language)
    if entry is not None:
        module_name, func_name = entry
        try:
            from tree_sitter import Language, Parser

            module = importlib.import_module(module_name)
            parser = Parser(Language(getattr(module, func_name)()))
        except Exception as e:  # noqa: BLE001 - una grammatica mancante non ferma le altre
            logger.debug("tree-sitter parser unavailable for %s: %s", language, e)
            parser = None
    _PARSER_CACHE[language] = parser
    return parser
```

Replace `_detect_language` (it rebuilds its `ext_map` on every call) with a module-level map:

```python
_EXT_LANGUAGES = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".go": "go", ".java": "java",
    ".cs": "csharp", ".php": "php", ".kt": "kotlin", ".kts": "kotlin",
    ".rs": "rust", ".sql": "sql",
}


def _detect_language(path: Path) -> str:
    """Detect programming language from file extension."""
    return _EXT_LANGUAGES.get(path.suffix.lower(), "text")
```

Lift the chunk node table out of `_extract_chunks_treesitter` to module level, immediately above that function, and add the depth map:

```python
# Nodi su cui spezzare un file sorgente. I linguaggi assenti usano DEF_NODES.
_CHUNK_NODES: dict[str, list[str]] = {
    "python": ["function_definition", "class_definition", "decorated_definition"],
    "javascript": ["function_declaration", "class_declaration", "arrow_function", "method_definition"],
    "typescript": ["function_declaration", "class_declaration", "interface_declaration", "type_alias_declaration"],
    "tsx": ["function_declaration", "class_declaration", "jsx_element"],
    "go": ["function_declaration", "method_declaration", "type_declaration"],
    "java": ["class_declaration", "method_declaration", "interface_declaration"],
}

# Profondita' massima a cui cercare un nodo da spezzare, per linguaggio.
_CHUNK_MAX_DEPTH: dict[str, int] = {}
```

Inside `_extract_chunks_treesitter`, delete the local `node_types = {...}` literal (now `_CHUNK_NODES`) and change the lines that used it:

```python
        target_types = set(_CHUNK_NODES.get(language) or DEF_NODES.get(language, ()))
        max_depth = _CHUNK_MAX_DEPTH.get(language, 2)

        def walk(node, depth=0):
            if node.type in target_types and depth <= max_depth:
```

Widen the name scan inside `walk`, so grammars that name a declaration with `type_identifier` (Rust) still get chunk metadata:

```python
                name = None
                for child in node.children:
                    if child.type in ("identifier", "name", "type_identifier"):
                        name = content[child.start_byte:child.end_byte]
                        break
```

Everything else in `_extract_chunks_treesitter` stays as it is.

- [ ] **Step 4: Add the override tables to `src/indexer/symbols.py`**

Directly under the existing `REF_NODES` set, add:

```python
# Nodi di riferimento aggiuntivi, per grammatiche che non usano ``identifier``.
REF_NODES_EXTRA: dict[str, set[str]] = {}

# Nodi il cui nome non sta nel campo ``name``: (linguaggio, nodo) -> campo.
NAME_FIELDS: dict[tuple[str, str], str] = {}

# Nodi il cui nome sta dentro un figlio: (linguaggio, nodo) -> tipo del figlio.
NAME_HOLDERS: dict[tuple[str, str], str] = {}

# Identificatori usati come ripiego quando il campo del nome manca.
IDENT_NODES = ("identifier", "type_identifier", "property_identifier")
```

Replace the existing `_name_of` function with:

```python
def _ref_nodes(language: str) -> set[str]:
    """Nodi che valgono come uso di un nome, per il linguaggio dato."""
    return REF_NODES | REF_NODES_EXTRA.get(language, set())


def _name_node(node, language: str = ""):
    """Nodo che porta il nome dichiarato da un nodo di definizione."""
    holder = NAME_HOLDERS.get((language, node.type))
    if holder:
        for child in node.children:
            if child.type == holder:
                node = child
                break
    field = NAME_FIELDS.get((language, node.type), "name")
    found = node.child_by_field_name(field) if hasattr(node, "child_by_field_name") else None
    if found is not None:
        return found
    for child in node.children:
        if child.type in IDENT_NODES:
            return child
    return None


def _name_of(node, source: bytes, language: str = "") -> Optional[str]:
    """Nome dichiarato da un nodo di definizione."""
    found = _name_node(node, language)
    if found is None:
        return None
    return source[found.start_byte : found.end_byte].decode("utf-8", "replace")
```

In `extract_symbols`, compute the reference set once and route both name lookups through the helpers. Change:

```python
    def_nodes = DEF_NODES[language]
```

to:

```python
    def_nodes = DEF_NODES[language]
    ref_nodes = _ref_nodes(language)
```

and replace this part of the walk loop:

```python
        if node.type in def_nodes:
            name = _name_of(node, source)
            if _keep(name):
                defs.append(
                    {"name": name, "kind": node.type, "line": node.start_point[0] + 1}
                )
                field = (
                    node.child_by_field_name("name")
                    if hasattr(node, "child_by_field_name")
                    else None
                )
                if field is not None:
                    def_name_spans.add((field.start_byte, field.end_byte))
        elif node.type in REF_NODES:
```

with:

```python
        if node.type in def_nodes:
            name = _name_of(node, source, language)
            if _keep(name):
                defs.append(
                    {"name": name, "kind": node.type, "line": node.start_point[0] + 1}
                )
                field = _name_node(node, language)
                if field is not None:
                    def_name_spans.add((field.start_byte, field.end_byte))
        elif node.type in ref_nodes:
```

- [ ] **Step 5: Run the new test and the existing suites**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_language_wiring.py tests/test_symbol_graph.py tests/test_symbol_graph_backfill.py tests/test_indexer_project_scope.py
```

Expected: PASS — 11 new tests plus the 19 + 4 + 4 existing ones.

- [ ] **Step 6: Commit**

```bash
git add services/server/src/indexer/indexer.py services/server/src/indexer/symbols.py services/server/tests/test_language_wiring.py
git commit -m "wire per-language parser loading and detection"
```

---

### Task 2: C# symbols

**Files:**
- Modify: `services/server/pyproject.toml` (add `tree-sitter-c-sharp>=0.23.5` to the "Indexing / code parsing" block)
- Modify: `services/server/src/indexer/indexer.py` (`_LANGUAGE_LOADERS`, `_CHUNK_MAX_DEPTH`)
- Modify: `services/server/src/indexer/symbols.py` (`DEF_NODES`)
- Test: `services/server/tests/test_symbols_csharp.py` (create)

**Interfaces:**
- Consumes (from Task 1):
  - `indexer._LANGUAGE_LOADERS: dict[str, tuple[str, str]]` — language key → `(module_name, language_function_name)`; `indexer._get_parser(language)` reads it, caches the result in `indexer._PARSER_CACHE`, and returns `None` when the import fails.
  - `indexer._EXT_LANGUAGES` already maps `".cs" -> "csharp"`, and `".cs"` is already in `indexer.PARSEABLE_EXTENSIONS`.
  - `indexer._CHUNK_NODES: dict[str, list[str]]` and `indexer._CHUNK_MAX_DEPTH: dict[str, int]` (default depth 2); `_extract_chunks_treesitter` uses `set(_CHUNK_NODES.get(language) or DEF_NODES.get(language, ()))`, so a language absent from `_CHUNK_NODES` chunks on its `DEF_NODES` set.
  - `symbols.extract_symbols(content: str, language: str, parser) -> {"defs": [{"name", "kind", "line"}], "refs": [{"name", "line"}]}`; names shorter than 4 characters or in `symbols.STOP_NAMES` are dropped from both lists.
- Produces: `symbols.DEF_NODES["csharp"]`; `indexer._LANGUAGE_LOADERS["csharp"] = ("tree_sitter_c_sharp", "language")`; `indexer._CHUNK_MAX_DEPTH["csharp"] = 3`.

**Why the depth override:** C# declarations live inside a `namespace_declaration` block, so the parse tree is `compilation_unit(0) → namespace_declaration(1) → declaration_list(2) → class_declaration(3)`. The default limit of 2 would never reach them and every `.cs` file would fall back to sliding-window chunks.

- [ ] **Step 1: Add the grammar dependency and install it**

In `services/server/pyproject.toml`, in the `# Indexing / code parsing` block that currently ends with `"tree-sitter-java>=0.23.0",`, add:

```toml
    "tree-sitter-c-sharp>=0.23.5",
```

Then, from `services/server`:

```
uv pip install --python .venv/Scripts/python.exe -e .
```

Verify the wheel is usable:

```
.venv/Scripts/python.exe -c "import tree_sitter_c_sharp; from tree_sitter import Language, Parser; print(Parser(Language(tree_sitter_c_sharp.language())))"
```

Expected: a `<tree_sitter.Parser object …>` line, no traceback.

- [ ] **Step 2: Write the failing test**

Create `services/server/tests/test_symbols_csharp.py`:

```python
"""Simboli C#: definizioni, riferimenti, import e chunking."""
from pathlib import Path

from src.config import IndexingConfig
from src.indexer import symbols
from src.indexer.indexer import _detect_language, _extract_chunks_treesitter, _get_parser

CS_SRC = '''using Scheduling.Services;

namespace Scheduling.Core
{
    public interface IOperatorLoadProvider
    {
        int AvailableSlots(string operatorCode);
    }

    public enum AssignmentOutcome { Assigned, Deferred, Rejected }

    public record OperatorSnapshot(string OperatorCode, int ActiveTickets);

    public struct SlotWindow
    {
        public int Capacity;
    }

    public class AssignmentScheduler : IOperatorLoadProvider
    {
        private readonly SchedulerService _schedulerService;

        public AssignmentScheduler(SchedulerService schedulerService)
        {
            _schedulerService = schedulerService;
        }

        public int MaxActiveTickets { get; set; }

        public int AvailableSlots(string operatorCode)
        {
            return _schedulerService.ComputeAvailableSlots(operatorCode);
        }
    }
}
'''

EXPECTED_DEFS = {
    ("IOperatorLoadProvider", "interface_declaration", 5),
    ("AvailableSlots", "method_declaration", 7),
    ("AssignmentOutcome", "enum_declaration", 10),
    ("OperatorSnapshot", "record_declaration", 12),
    ("SlotWindow", "struct_declaration", 14),
    ("AssignmentScheduler", "class_declaration", 19),
    ("AssignmentScheduler", "constructor_declaration", 23),
    ("MaxActiveTickets", "property_declaration", 28),
    ("AvailableSlots", "method_declaration", 30),
}


def _extract():
    parser = _get_parser("csharp")
    assert parser is not None, (
        "grammatica C# assente: uv pip install --python .venv/Scripts/python.exe -e ."
    )
    return symbols.extract_symbols(CS_SRC, "csharp", parser)


def test_cs_files_are_detected_as_csharp():
    assert _detect_language(Path("Scheduling/AssignmentScheduler.cs")) == "csharp"


def test_extracts_every_csharp_definition_kind():
    out = _extract()
    got = {(d["name"], d["kind"], d["line"]) for d in out["defs"]}
    assert got == EXPECTED_DEFS


def test_extracts_a_call_reference():
    out = _extract()
    assert {"name": "ComputeAvailableSlots", "line": 32} in out["refs"]


def test_extracts_the_using_directive():
    out = _extract()
    assert {"name": "Services", "line": 1} in out["refs"]


def test_declared_names_are_not_also_references():
    out = _extract()
    defs_at = {(d["name"], d["line"]) for d in out["defs"]}
    refs_at = {(r["name"], r["line"]) for r in out["refs"]}
    assert not (defs_at & refs_at)


def test_namespaced_declarations_are_chunked():
    chunks = _extract_chunks_treesitter(CS_SRC, "csharp", IndexingConfig())
    assert ("class_declaration", "AssignmentScheduler") in {
        (c["type"], c["name"]) for c in chunks
    }


def test_broken_csharp_does_not_raise():
    parser = _get_parser("csharp")
    out = symbols.extract_symbols("public class Rotta {", "csharp", parser)
    assert isinstance(out["defs"], list)
```

- [ ] **Step 3: Run the test to verify it fails**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbols_csharp.py
```

Expected: FAIL — `_get_parser("csharp")` returns `None` (no `_LANGUAGE_LOADERS` entry yet), so the assertion in `_extract` fires.

- [ ] **Step 4: Register the grammar, the definition nodes and the chunk depth**

In `src/indexer/indexer.py`, add the loader row to `_LANGUAGE_LOADERS` after `"java"`:

```python
    "csharp": ("tree_sitter_c_sharp", "language"),
```

and give C# a deeper chunk limit:

```python
# Profondita' massima a cui cercare un nodo da spezzare, per linguaggio.
_CHUNK_MAX_DEPTH: dict[str, int] = {"csharp": 3}
```

In `src/indexer/symbols.py`, add the entry to `DEF_NODES` after `"java"`:

```python
    "csharp": {
        "class_declaration",
        "interface_declaration",
        "struct_declaration",
        "record_declaration",
        "enum_declaration",
        "method_declaration",
        "constructor_declaration",
        "property_declaration",
    },
```

No `_CHUNK_NODES` entry: C# chunks on its `DEF_NODES` set, which is the single source of truth the spec asks for.

- [ ] **Step 5: Run the test to verify it passes**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbols_csharp.py tests/test_language_wiring.py tests/test_symbol_graph.py
```

Expected: PASS (7 + 11 + 19).

- [ ] **Step 6: Commit**

```bash
git add services/server/pyproject.toml services/server/src/indexer/indexer.py services/server/src/indexer/symbols.py services/server/tests/test_symbols_csharp.py
git commit -m "extract C# symbols with tree-sitter"
```

---

### Task 3: PHP symbols

**Files:**
- Modify: `services/server/pyproject.toml` (add `tree-sitter-php>=0.24.1`)
- Modify: `services/server/src/indexer/indexer.py` (`_LANGUAGE_LOADERS`)
- Modify: `services/server/src/indexer/symbols.py` (`DEF_NODES`, `REF_NODES_EXTRA`)
- Test: `services/server/tests/test_symbols_php.py` (create)

**Interfaces:**
- Consumes (from Task 1):
  - `indexer._LANGUAGE_LOADERS: dict[str, tuple[str, str]]` — language key → `(module_name, language_function_name)`; `indexer._get_parser(language)` reads it and returns `None` when the import fails.
  - `indexer._EXT_LANGUAGES` already maps `".php" -> "php"`, and `".php"` is already in `indexer.PARSEABLE_EXTENSIONS`.
  - `symbols.REF_NODES = {"identifier", "type_identifier", "field_identifier", "property_identifier"}` and `symbols.REF_NODES_EXTRA: dict[str, set[str]]`; `symbols._ref_nodes(language)` returns `REF_NODES | REF_NODES_EXTRA.get(language, set())` and `extract_symbols` uses it to decide what counts as a reference.
  - `symbols.extract_symbols(content, language, parser) -> {"defs": [{"name", "kind", "line"}], "refs": [{"name", "line"}]}`; names shorter than 4 characters or in `symbols.STOP_NAMES` are dropped.
- Produces: `symbols.DEF_NODES["php"]`; `symbols.REF_NODES_EXTRA["php"] = {"name"}`; `indexer._LANGUAGE_LOADERS["php"] = ("tree_sitter_php", "language_php")`.

**Why the extra reference node:** the PHP grammar does not use `identifier` — every identifier leaf (class names, method names, imported names) has node type `name`. Without `REF_NODES_EXTRA["php"] = {"name"}` PHP files would produce definitions and zero references, and the graph would have no edges. `name` is added per language, not to the global `REF_NODES`, so the other grammars are untouched.

Use `tree_sitter_php.language_php()`, not `language_php_only()`: the `php` language parses a file that starts with HTML before `<?php`, which is what real PHP repositories contain.

- [ ] **Step 1: Add the grammar dependency and install it**

In `services/server/pyproject.toml`, in the `# Indexing / code parsing` block, add:

```toml
    "tree-sitter-php>=0.24.1",
```

Then, from `services/server`:

```
uv pip install --python .venv/Scripts/python.exe -e .
```

Verify:

```
.venv/Scripts/python.exe -c "import tree_sitter_php; from tree_sitter import Language, Parser; print(Parser(Language(tree_sitter_php.language_php())))"
```

Expected: a `<tree_sitter.Parser object …>` line, no traceback.

- [ ] **Step 2: Write the failing test**

Create `services/server/tests/test_symbols_php.py`. The source snippet is a **raw** string (`r'''…'''`) so the namespace backslashes survive:

```python
"""Simboli PHP: definizioni, riferimenti, import."""
from pathlib import Path

from src.indexer import symbols
from src.indexer.indexer import _detect_language, _get_parser

PHP_SRC = r'''<?php

namespace Scheduling\Core;

use Scheduling\Services\SchedulerService;

interface OperatorLoadProvider
{
    public function availableSlots(string $operatorCode): int;
}

trait LoggableScheduler
{
    public function logDecision(string $decision): void
    {
        error_log($decision);
    }
}

enum AssignmentOutcome: string
{
    case Assigned = 'assigned';
}

class AssignmentScheduler implements OperatorLoadProvider
{
    public function availableSlots(string $operatorCode): int
    {
        $service = new SchedulerService();
        return $service->computeAvailableSlots($operatorCode);
    }
}

function buildScheduler(): AssignmentScheduler
{
    return new AssignmentScheduler();
}
'''

EXPECTED_DEFS = {
    ("OperatorLoadProvider", "interface_declaration", 7),
    ("availableSlots", "method_declaration", 9),
    ("LoggableScheduler", "trait_declaration", 12),
    ("logDecision", "method_declaration", 14),
    ("AssignmentOutcome", "enum_declaration", 20),
    ("AssignmentScheduler", "class_declaration", 25),
    ("availableSlots", "method_declaration", 27),
    ("buildScheduler", "function_definition", 34),
}


def _extract():
    parser = _get_parser("php")
    assert parser is not None, (
        "grammatica PHP assente: uv pip install --python .venv/Scripts/python.exe -e ."
    )
    return symbols.extract_symbols(PHP_SRC, "php", parser)


def test_php_files_are_detected_as_php():
    assert _detect_language(Path("src/AssignmentScheduler.php")) == "php"


def test_php_identifiers_count_as_references():
    assert symbols._ref_nodes("php") == symbols.REF_NODES | {"name"}


def test_extracts_every_php_definition_kind():
    out = _extract()
    got = {(d["name"], d["kind"], d["line"]) for d in out["defs"]}
    assert got == EXPECTED_DEFS


def test_extracts_a_method_call_reference():
    out = _extract()
    assert {"name": "computeAvailableSlots", "line": 30} in out["refs"]


def test_extracts_the_use_declaration():
    out = _extract()
    assert {"name": "SchedulerService", "line": 5} in out["refs"]


def test_declared_names_are_not_also_references():
    out = _extract()
    defs_at = {(d["name"], d["line"]) for d in out["defs"]}
    refs_at = {(r["name"], r["line"]) for r in out["refs"]}
    assert not (defs_at & refs_at)


def test_broken_php_does_not_raise():
    parser = _get_parser("php")
    out = symbols.extract_symbols("<?php class Rotta {", "php", parser)
    assert isinstance(out["defs"], list)
```

- [ ] **Step 3: Run the test to verify it fails**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbols_php.py
```

Expected: FAIL — `_get_parser("php")` returns `None`, and `symbols._ref_nodes("php")` still equals `REF_NODES`.

- [ ] **Step 4: Register the grammar, the definition nodes and the extra reference node**

In `src/indexer/indexer.py`, add to `_LANGUAGE_LOADERS`:

```python
    "php": ("tree_sitter_php", "language_php"),
```

In `src/indexer/symbols.py`, add to `DEF_NODES`:

```python
    "php": {
        "class_declaration",
        "interface_declaration",
        "trait_declaration",
        "enum_declaration",
        "function_definition",
        "method_declaration",
    },
```

and fill `REF_NODES_EXTRA`:

```python
# Nodi di riferimento aggiuntivi, per grammatiche che non usano ``identifier``.
REF_NODES_EXTRA: dict[str, set[str]] = {"php": {"name"}}
```

- [ ] **Step 5: Run the test to verify it passes**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbols_php.py tests/test_language_wiring.py tests/test_symbol_graph.py
```

Expected: PASS (7 + 11 + 19).

- [ ] **Step 6: Commit**

```bash
git add services/server/pyproject.toml services/server/src/indexer/indexer.py services/server/src/indexer/symbols.py services/server/tests/test_symbols_php.py
git commit -m "extract PHP symbols with tree-sitter"
```

---

### Task 4: Kotlin symbols

**Files:**
- Modify: `services/server/pyproject.toml` (add `tree-sitter-kotlin>=1.1.0`)
- Modify: `services/server/src/indexer/indexer.py` (`_LANGUAGE_LOADERS`)
- Modify: `services/server/src/indexer/symbols.py` (`DEF_NODES`, `NAME_HOLDERS`)
- Test: `services/server/tests/test_symbols_kotlin.py` (create)

**Interfaces:**
- Consumes (from Task 1):
  - `indexer._LANGUAGE_LOADERS: dict[str, tuple[str, str]]` — language key → `(module_name, language_function_name)`; `indexer._get_parser(language)` reads it and returns `None` when the import fails.
  - `indexer._EXT_LANGUAGES` already maps `".kt" -> "kotlin"` and `".kts" -> "kotlin"`; both are already in `indexer.PARSEABLE_EXTENSIONS`.
  - `symbols.NAME_HOLDERS: dict[tuple[str, str], str]` — `(language, node_type)` → type of the child node that actually holds the name. `symbols._name_node(node, language)` first hops into that child, then reads `child_by_field_name(NAME_FIELDS.get((language, node.type), "name"))`, then falls back to the first child whose type is in `symbols.IDENT_NODES = ("identifier", "type_identifier", "property_identifier")`.
  - `symbols.extract_symbols(content, language, parser) -> {"defs": [{"name", "kind", "line"}], "refs": [{"name", "line"}]}`; names shorter than 4 characters or in `symbols.STOP_NAMES` are dropped.
- Produces: `symbols.DEF_NODES["kotlin"]`; `symbols.NAME_HOLDERS[("kotlin", "property_declaration")] = "variable_declaration"`; `indexer._LANGUAGE_LOADERS["kotlin"] = ("tree_sitter_kotlin", "language")`.

**Two grammar facts to expect:**
1. `property_declaration` has **no** `name` field: the parse tree is `property_declaration → val, variable_declaration → identifier`. Without the `NAME_HOLDERS` hop every Kotlin `val`/`var` would be silently dropped, so `defaultScheduler` and `maxActiveTickets` are the tests that prove the hop works.
2. `tree-sitter-kotlin` reports a Kotlin `interface` as `class_declaration`, not `interface_declaration`. That is why `OperatorLoadProvider` is expected with kind `class_declaration` below, and why `interface_declaration` is not in the node set.

- [ ] **Step 1: Add the grammar dependency and install it**

In `services/server/pyproject.toml`, in the `# Indexing / code parsing` block, add:

```toml
    "tree-sitter-kotlin>=1.1.0",
```

Then, from `services/server`:

```
uv pip install --python .venv/Scripts/python.exe -e .
```

Verify:

```
.venv/Scripts/python.exe -c "import tree_sitter_kotlin; from tree_sitter import Language, Parser; print(Parser(Language(tree_sitter_kotlin.language())))"
```

Expected: a `<tree_sitter.Parser object …>` line, no traceback.

- [ ] **Step 2: Write the failing test**

Create `services/server/tests/test_symbols_kotlin.py`:

```python
"""Simboli Kotlin: definizioni, riferimenti, import."""
from pathlib import Path

from src.indexer import symbols
from src.indexer.indexer import _detect_language, _get_parser

KT_SRC = '''package scheduling.core

import scheduling.services.SchedulerService

interface OperatorLoadProvider {
    fun availableSlots(operatorCode: String): Int
}

object SchedulerRegistry {
    val defaultScheduler = AssignmentScheduler()
}

class AssignmentScheduler : OperatorLoadProvider {
    val maxActiveTickets: Int = 12

    override fun availableSlots(operatorCode: String): Int {
        return SchedulerService.computeAvailableSlots(operatorCode)
    }
}
'''

EXPECTED_DEFS = {
    ("OperatorLoadProvider", "class_declaration", 5),
    ("availableSlots", "function_declaration", 6),
    ("SchedulerRegistry", "object_declaration", 9),
    ("defaultScheduler", "property_declaration", 10),
    ("AssignmentScheduler", "class_declaration", 13),
    ("maxActiveTickets", "property_declaration", 14),
    ("availableSlots", "function_declaration", 16),
}


def _extract():
    parser = _get_parser("kotlin")
    assert parser is not None, (
        "grammatica Kotlin assente: uv pip install --python .venv/Scripts/python.exe -e ."
    )
    return symbols.extract_symbols(KT_SRC, "kotlin", parser)


def test_kotlin_extensions_are_detected():
    assert _detect_language(Path("core/AssignmentScheduler.kt")) == "kotlin"
    assert _detect_language(Path("build.gradle.kts")) == "kotlin"


def test_extracts_every_kotlin_definition_kind():
    out = _extract()
    got = {(d["name"], d["kind"], d["line"]) for d in out["defs"]}
    assert got == EXPECTED_DEFS


def test_property_names_come_from_the_variable_declaration():
    out = _extract()
    properties = {d["name"] for d in out["defs"] if d["kind"] == "property_declaration"}
    assert properties == {"defaultScheduler", "maxActiveTickets"}


def test_extracts_a_call_reference():
    out = _extract()
    assert {"name": "computeAvailableSlots", "line": 17} in out["refs"]


def test_extracts_the_import_header():
    out = _extract()
    assert {"name": "SchedulerService", "line": 3} in out["refs"]


def test_declared_names_are_not_also_references():
    out = _extract()
    defs_at = {(d["name"], d["line"]) for d in out["defs"]}
    refs_at = {(r["name"], r["line"]) for r in out["refs"]}
    assert not (defs_at & refs_at)


def test_broken_kotlin_does_not_raise():
    parser = _get_parser("kotlin")
    out = symbols.extract_symbols("class Rotta {", "kotlin", parser)
    assert isinstance(out["defs"], list)
```

- [ ] **Step 3: Run the test to verify it fails**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbols_kotlin.py
```

Expected: FAIL — `_get_parser("kotlin")` returns `None`, so `_extract` asserts.

- [ ] **Step 4: Register the grammar, the definition nodes and the name holder**

In `src/indexer/indexer.py`, add to `_LANGUAGE_LOADERS`:

```python
    "kotlin": ("tree_sitter_kotlin", "language"),
```

In `src/indexer/symbols.py`, add to `DEF_NODES`:

```python
    "kotlin": {
        "class_declaration",
        "object_declaration",
        "function_declaration",
        "property_declaration",
    },
```

and fill `NAME_HOLDERS`:

```python
# Nodi il cui nome sta dentro un figlio: (linguaggio, nodo) -> tipo del figlio.
NAME_HOLDERS: dict[tuple[str, str], str] = {
    ("kotlin", "property_declaration"): "variable_declaration",
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbols_kotlin.py tests/test_language_wiring.py tests/test_symbol_graph.py
```

Expected: PASS (7 + 11 + 19).

- [ ] **Step 6: Commit**

```bash
git add services/server/pyproject.toml services/server/src/indexer/indexer.py services/server/src/indexer/symbols.py services/server/tests/test_symbols_kotlin.py
git commit -m "extract Kotlin symbols with tree-sitter"
```

---

### Task 5: Rust symbols

**Files:**
- Modify: `services/server/pyproject.toml` (add `tree-sitter-rust>=0.24.2`)
- Modify: `services/server/src/indexer/indexer.py` (`_LANGUAGE_LOADERS`)
- Modify: `services/server/src/indexer/symbols.py` (`DEF_NODES`, `NAME_FIELDS`)
- Test: `services/server/tests/test_symbols_rust.py` (create)

**Interfaces:**
- Consumes (from Task 1):
  - `indexer._LANGUAGE_LOADERS: dict[str, tuple[str, str]]` — language key → `(module_name, language_function_name)`; `indexer._get_parser(language)` reads it and returns `None` when the import fails.
  - `indexer._EXT_LANGUAGES` already maps `".rs" -> "rust"`, and `".rs"` is already in `indexer.PARSEABLE_EXTENSIONS`.
  - `symbols.NAME_FIELDS: dict[tuple[str, str], str]` — `(language, node_type)` → the tree-sitter **field** holding the declared name; the default when there is no entry is `"name"`. `symbols._name_node(node, language)` reads `node.child_by_field_name(NAME_FIELDS.get((language, node.type), "name"))` and only then falls back to the first child in `symbols.IDENT_NODES = ("identifier", "type_identifier", "property_identifier")`.
  - `symbols.extract_symbols(content, language, parser) -> {"defs": [{"name", "kind", "line"}], "refs": [{"name", "line"}]}`; names shorter than 4 characters or in `symbols.STOP_NAMES` are dropped.
- Produces: `symbols.DEF_NODES["rust"]`; `symbols.NAME_FIELDS[("rust", "impl_item")] = "type"`; `indexer._LANGUAGE_LOADERS["rust"] = ("tree_sitter_rust", "language")`.

**Why the field override:** `impl OperatorLoadProvider for AssignmentScheduler` has no `name` field — it has a `trait` field (`OperatorLoadProvider`) and a `type` field (`AssignmentScheduler`). Without the override the fallback picks the first identifier child, which is the **trait**, so every impl block would be filed under the trait it implements instead of the type it belongs to. With `NAME_FIELDS[("rust", "impl_item")] = "type"` the block is named `AssignmentScheduler`, matching the struct it extends.

- [ ] **Step 1: Add the grammar dependency and install it**

In `services/server/pyproject.toml`, in the `# Indexing / code parsing` block, add:

```toml
    "tree-sitter-rust>=0.24.2",
```

Then, from `services/server`:

```
uv pip install --python .venv/Scripts/python.exe -e .
```

Verify:

```
.venv/Scripts/python.exe -c "import tree_sitter_rust; from tree_sitter import Language, Parser; print(Parser(Language(tree_sitter_rust.language())))"
```

Expected: a `<tree_sitter.Parser object …>` line, no traceback.

- [ ] **Step 2: Write the failing test**

Create `services/server/tests/test_symbols_rust.py`:

```python
"""Simboli Rust: definizioni, riferimenti, import."""
from pathlib import Path

from src.indexer import symbols
from src.indexer.indexer import _detect_language, _get_parser

RS_SRC = '''use crate::services::scheduler_service;

pub type OperatorCode = String;

pub mod assignment_helpers {
    pub const DEFAULT_CAPACITY: usize = 12;
}

pub trait OperatorLoadProvider {
    fn available_slots(&self, operator_code: &str) -> usize;
}

pub enum AssignmentOutcome {
    Assigned,
    Deferred,
}

pub struct AssignmentScheduler {
    pub max_active_tickets: usize,
}

impl OperatorLoadProvider for AssignmentScheduler {
    fn available_slots(&self, operator_code: &str) -> usize {
        scheduler_service::compute_available_slots(operator_code)
    }
}

fn build_scheduler() -> AssignmentScheduler {
    AssignmentScheduler { max_active_tickets: 12 }
}
'''

EXPECTED_DEFS = {
    ("OperatorCode", "type_item", 3),
    ("assignment_helpers", "mod_item", 5),
    ("OperatorLoadProvider", "trait_item", 9),
    ("AssignmentOutcome", "enum_item", 13),
    ("AssignmentScheduler", "struct_item", 18),
    ("AssignmentScheduler", "impl_item", 22),
    ("available_slots", "function_item", 23),
    ("build_scheduler", "function_item", 28),
}


def _extract():
    parser = _get_parser("rust")
    assert parser is not None, (
        "grammatica Rust assente: uv pip install --python .venv/Scripts/python.exe -e ."
    )
    return symbols.extract_symbols(RS_SRC, "rust", parser)


def test_rs_files_are_detected_as_rust():
    assert _detect_language(Path("src/scheduler.rs")) == "rust"


def test_extracts_every_rust_definition_kind():
    out = _extract()
    got = {(d["name"], d["kind"], d["line"]) for d in out["defs"]}
    assert got == EXPECTED_DEFS


def test_an_impl_block_is_named_after_its_type_not_its_trait():
    out = _extract()
    impls = {d["name"] for d in out["defs"] if d["kind"] == "impl_item"}
    assert impls == {"AssignmentScheduler"}


def test_extracts_a_call_reference():
    out = _extract()
    assert {"name": "compute_available_slots", "line": 24} in out["refs"]


def test_extracts_the_use_declaration():
    out = _extract()
    assert {"name": "scheduler_service", "line": 1} in out["refs"]


def test_declared_names_are_not_also_references():
    out = _extract()
    defs_at = {(d["name"], d["line"]) for d in out["defs"]}
    refs_at = {(r["name"], r["line"]) for r in out["refs"]}
    assert not (defs_at & refs_at)


def test_broken_rust_does_not_raise():
    parser = _get_parser("rust")
    out = symbols.extract_symbols("struct Rotta {", "rust", parser)
    assert isinstance(out["defs"], list)
```

- [ ] **Step 3: Run the test to verify it fails**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbols_rust.py
```

Expected: FAIL — `_get_parser("rust")` returns `None`, so `_extract` asserts.

- [ ] **Step 4: Register the grammar, the definition nodes and the name field**

In `src/indexer/indexer.py`, add to `_LANGUAGE_LOADERS`:

```python
    "rust": ("tree_sitter_rust", "language"),
```

In `src/indexer/symbols.py`, add to `DEF_NODES`:

```python
    "rust": {
        "function_item",
        "struct_item",
        "enum_item",
        "trait_item",
        "impl_item",
        "type_item",
        "mod_item",
    },
```

and fill `NAME_FIELDS`:

```python
# Nodi il cui nome non sta nel campo ``name``: (linguaggio, nodo) -> campo.
NAME_FIELDS: dict[tuple[str, str], str] = {
    ("rust", "impl_item"): "type",
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbols_rust.py tests/test_language_wiring.py tests/test_symbol_graph.py
```

Expected: PASS (7 + 11 + 19).

- [ ] **Step 6: Commit**

```bash
git add services/server/pyproject.toml services/server/src/indexer/indexer.py services/server/src/indexer/symbols.py services/server/tests/test_symbols_rust.py
git commit -m "extract Rust symbols with tree-sitter"
```

---

### Task 6: SQL definitions from a regex extractor

No maintained SQL grammar ships reliable wheels, so `.sql` files keep the text chunking path they already have (`.sql` is in `TEXT_EXTENSIONS`, never in `PARSEABLE_EXTENSIONS`) and get their definitions from a regex over `CREATE …` statements. SQL produces definitions only; references are not extracted.

**Files:**
- Modify: `services/server/src/indexer/symbols.py` (`extract_sql_symbols`, `is_symbol_language`, dispatch in `extract_symbols`)
- Modify: `services/server/src/indexer/indexer.py` (`collect_symbols_sync`)
- Test: `services/server/tests/test_symbols_sql.py` (create)

**Interfaces:**
- Consumes (from Task 1):
  - `indexer._EXT_LANGUAGES` already maps `".sql" -> "sql"`, and `.sql` is in `indexer.TEXT_EXTENSIONS`.
  - `symbols._keep(name) -> bool` — `True` when `name` is non-empty, at least `symbols.MIN_NAME_LEN` (4) characters long, and its lowercase form is not in `symbols.STOP_NAMES` (which contains, among others, `data`, `type`, `key`, `item`, `list`, `map`, `result`, `error`, `name`, `index`).
  - `symbols.extract_symbols(content: str, language: str, parser) -> dict` — currently returns `{"defs": [], "refs": []}` whenever `parser is None or language not in DEF_NODES`.
  - `indexer.collect_symbols_sync(local_path: str, indexing_cfg: IndexingConfig, language: str | None, only_paths: list[str] | None) -> list[dict]` — walks the repo with `_iter_repo_files`, and for each file emits one row per `(name, kind)` shaped `{"file_path", "name", "kind": "def" | "ref", "node_type", "line", "occurrences"}`, where `node_type` is the definition kind reported by `extract_symbols`.
- Produces:
  - `symbols.extract_sql_symbols(content: str) -> {"defs": [{"name", "kind", "line"}], "refs": []}`.
  - `symbols.is_symbol_language(language: str) -> bool`.

**Current code you are changing (`collect_symbols_sync`, `services/server/src/indexer/indexer.py`):**

```python
        lang = language or _detect_language(file_path)
        if lang not in symbols_mod.DEF_NODES:
            continue
        if lang not in parsers:
            parsers[lang] = _get_parser(lang)
        parser = parsers[lang]
        if parser is None:
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001 - un file illeggibile non ferma il resto
            continue
```

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_symbols_sql.py`:

```python
"""Definizioni SQL da regex: nessuna grammatica tree-sitter mantenuta le copre."""
from src.config import IndexingConfig
from src.indexer import indexer, symbols

SQL_SRC = '''-- schema di esempio
CREATE TABLE IF NOT EXISTS analytics.operator_load (
    operator_code TEXT PRIMARY KEY,
    active_tickets INTEGER NOT NULL
);

CREATE TABLE "Assignment Audit" (
    audit_id BIGSERIAL PRIMARY KEY
);

CREATE OR REPLACE VIEW available_slots_view AS
SELECT operator_code FROM analytics.operator_load;

CREATE MATERIALIZED VIEW analytics.daily_assignment_totals AS
SELECT count(*) FROM analytics.operator_load;

CREATE OR REPLACE FUNCTION compute_available_slots(p_operator TEXT)
RETURNS INTEGER AS $$ BEGIN RETURN 0; END; $$ LANGUAGE plpgsql;

CREATE PROCEDURE rebalance_assignments() LANGUAGE plpgsql AS $$ BEGIN END; $$;

CREATE TRIGGER assignment_audit_trigger AFTER INSERT ON analytics.operator_load
FOR EACH ROW EXECUTE FUNCTION compute_available_slots();

CREATE INDEX idx_operator_load_code ON analytics.operator_load (operator_code);

CREATE TYPE assignment_outcome AS ENUM ('assigned', 'deferred');
'''

EXPECTED_DEFS = {
    ("operator_load", "table", 2),
    ("Assignment Audit", "table", 7),
    ("available_slots_view", "view", 11),
    ("daily_assignment_totals", "materialized_view", 14),
    ("compute_available_slots", "function", 17),
    ("rebalance_assignments", "procedure", 20),
    ("assignment_audit_trigger", "trigger", 22),
    ("idx_operator_load_code", "index", 25),
    ("assignment_outcome", "type", 27),
}


def test_extracts_every_create_statement():
    out = symbols.extract_sql_symbols(SQL_SRC)
    got = {(d["name"], d["kind"], d["line"]) for d in out["defs"]}
    assert got == EXPECTED_DEFS


def test_sql_has_no_references():
    assert symbols.extract_sql_symbols(SQL_SRC)["refs"] == []


def test_schema_prefix_and_quotes_are_stripped():
    out = symbols.extract_sql_symbols(
        'create table if not exists "sales"."Order Items" (id INT);'
    )
    assert out["defs"] == [{"name": "Order Items", "kind": "table", "line": 1}]


def test_backticked_and_bracketed_names_are_unquoted():
    mysql = symbols.extract_sql_symbols("CREATE TABLE `order_items` (id INT);")
    mssql = symbols.extract_sql_symbols("CREATE TABLE [order_items] (id INT);")
    assert mysql["defs"] == [{"name": "order_items", "kind": "table", "line": 1}]
    assert mssql["defs"] == [{"name": "order_items", "kind": "table", "line": 1}]


def test_short_and_generic_names_are_dropped():
    out = symbols.extract_sql_symbols("CREATE TABLE ab (x INT);\nCREATE TABLE data (x INT);\n")
    assert out["defs"] == []


def test_statements_that_are_not_create_are_ignored():
    out = symbols.extract_sql_symbols(
        "DROP TABLE analytics.operator_load;\nSELECT * FROM analytics.operator_load;\n"
    )
    assert out["defs"] == []


def test_sql_is_a_symbol_language_without_a_parser():
    assert symbols.is_symbol_language("sql") is True
    assert symbols.is_symbol_language("python") is True
    assert symbols.is_symbol_language("text") is False


def test_extract_symbols_dispatches_sql_without_a_parser():
    assert symbols.extract_symbols(SQL_SRC, "sql", None) == symbols.extract_sql_symbols(SQL_SRC)


def test_the_collector_reads_sql_files(tmp_path):
    (tmp_path / "schema.sql").write_text(SQL_SRC, encoding="utf-8")
    rows = indexer.collect_symbols_sync(str(tmp_path), IndexingConfig(), None, None)
    by_name = {r["name"]: r for r in rows}
    assert by_name["operator_load"]["kind"] == "def"
    assert by_name["operator_load"]["node_type"] == "table"
    assert by_name["operator_load"]["file_path"] == "schema.sql"
    assert all(r["kind"] == "def" for r in rows)
```

Note the `$$` dollar-quoting inside `SQL_SRC`: it is inside an ordinary Python string, so nothing escapes it, and the regex never matches it because it is not preceded by `CREATE`.

- [ ] **Step 2: Run the test to verify it fails**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbols_sql.py
```

Expected: FAIL — `AttributeError: module 'src.indexer.symbols' has no attribute 'extract_sql_symbols'`.

- [ ] **Step 3: Add the SQL extractor to `src/indexer/symbols.py`**

Add `import re` to the imports at the top of the module (it currently imports only `collections.defaultdict` and `typing`).

Add, immediately below the `MIN_NAME_LEN = 4` constant:

```python
# SQL non ha una grammatica tree-sitter mantenuta: le definizioni si leggono
# dagli statement CREATE, i riferimenti non si estraggono.
_SQL_IDENT = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$]*)'
_SQL_CREATE_RE = re.compile(
    r"\bcreate\s+(?:or\s+replace\s+)?"
    r"(materialized\s+view|table|view|function|procedure|trigger|index|type)\s+"
    r"(?:if\s+not\s+exists\s+)?"
    rf"((?:{_SQL_IDENT}\s*\.\s*)*{_SQL_IDENT})",
    re.IGNORECASE,
)
_SQL_IDENT_RE = re.compile(_SQL_IDENT)
```

Add these two functions next to `extract_symbols` (order inside the module does not matter as long as they are defined before use at call time):

```python
def is_symbol_language(language: str) -> bool:
    """Il linguaggio produce simboli: grammatica tree-sitter o estrattore regex."""
    return language in DEF_NODES or language == "sql"


def _sql_name(raw: str) -> Optional[str]:
    """Ultimo segmento di un nome qualificato, senza virgolette."""
    parts = _SQL_IDENT_RE.findall(raw)
    if not parts:
        return None
    last = parts[-1]
    return last[1:-1] if last[0] in '"`[' else last


def extract_sql_symbols(content: str) -> dict[str, list[dict]]:
    """Definizioni SQL dagli statement CREATE. Nessun riferimento."""
    defs: list[dict] = []
    for match in _SQL_CREATE_RE.finditer(content):
        name = _sql_name(match.group(2))
        if not _keep(name):
            continue
        defs.append(
            {
                "name": name,
                "kind": "_".join(match.group(1).lower().split()),
                "line": content.count("\n", 0, match.start()) + 1,
            }
        )
    return {"defs": defs, "refs": []}
```

Then dispatch from `extract_symbols`. Its body currently starts with:

```python
    if parser is None or language not in DEF_NODES:
        return {"defs": [], "refs": []}
```

Change that to:

```python
    if language == "sql":
        return extract_sql_symbols(content)
    if parser is None or language not in DEF_NODES:
        return {"defs": [], "refs": []}
```

- [ ] **Step 4: Let `collect_symbols_sync` read SQL files without a parser**

In `src/indexer/indexer.py`, inside `collect_symbols_sync`, replace the language/parser guard shown in the "Current code you are changing" block above with:

```python
        lang = language or _detect_language(file_path)
        if not symbols_mod.is_symbol_language(lang):
            continue
        parser = None
        if lang in symbols_mod.DEF_NODES:
            if lang not in parsers:
                parsers[lang] = _get_parser(lang)
            parser = parsers[lang]
            if parser is None:
                continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001 - un file illeggibile non ferma il resto
            continue
```

The rest of the loop (the call to `symbols_mod.extract_symbols(content, lang, parser)` and the `counted` bookkeeping) is unchanged: with `lang == "sql"` and `parser is None`, `extract_symbols` dispatches to the regex extractor.

- [ ] **Step 5: Run the test to verify it passes**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbols_sql.py tests/test_language_wiring.py tests/test_symbol_graph.py tests/test_symbol_graph_backfill.py
```

Expected: PASS (9 + 11 + 19 + 4).

- [ ] **Step 6: Commit**

```bash
git add services/server/src/indexer/symbols.py services/server/src/indexer/indexer.py services/server/tests/test_symbols_sql.py
git commit -m "extract SQL definitions with a regex"
```

---

### Task 7: Symbol rows for a new language, README, full suite

The per-language tasks proved extraction in isolation. This task proves the whole path — walk the repo, detect the language, extract, count occurrences, write rows through `_store_symbols` — reaches the database for a `.cs` file, using a fake pool so no database is needed. Then it updates the README and runs the entire server suite.

**Files:**
- Test: `services/server/tests/test_symbol_rows_persist.py` (create)
- Modify: `README.md` (repository root, lines 10 and 31)

**Interfaces:**
- Consumes (from Tasks 1–6):
  - `indexer.collect_symbols_sync(local_path: str, indexing_cfg: IndexingConfig, language: str | None, only_paths: list[str] | None) -> list[dict]` — one row per `(name, kind)` per file, shaped `{"file_path", "name", "kind": "def" | "ref", "node_type", "line", "occurrences"}`.
  - `async indexer._store_symbols(pool, org_id: int, project_id: int, repo_name: str, rows: list[dict], replace_paths: list[str] | None = None) -> None` — inside `pool.acquire()` and `conn.transaction()`, runs one `DELETE FROM repo_symbols …` and then `conn.executemany(<INSERT …>, [(org_id, project_id, repo_name, file_path, name, kind, node_type, line, occurrences), …])` in batches of 1000.
  - `symbols.DEF_NODES["csharp"]` contains `class_declaration` and `method_declaration`; `indexer._detect_language(Path("x.cs")) == "csharp"`.
  - `src.config.IndexingConfig()` with its defaults is enough for `_iter_repo_files`.
- Produces: nothing consumed by later tasks.

**Fake-pool pattern to follow** (same shape as `tests/test_symbol_graph_backfill.py` and `tests/test_role_permissions.py`): a `_FakeConn` recording `execute`/`executemany`, a `transaction()` returning an async context manager, and a `_FakePool.acquire()` returning an async context manager yielding the connection.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_symbol_rows_persist.py`:

```python
"""Dal file sorgente alle righe di repo_symbols, senza database.

Prova che un linguaggio aggiunto arrivi fino alla persistenza: raccolta,
conteggio delle occorrenze e INSERT con i parametri giusti.
"""
import asyncio

from src.config import IndexingConfig
from src.indexer import indexer

CS_SRC = """namespace Scheduling.Core
{
    public class AssignmentScheduler
    {
        public int AvailableSlots(string operatorCode)
        {
            return SchedulerService.ComputeAvailableSlots(operatorCode);
        }
    }
}
"""


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self):
        self.executed = []
        self.inserted = []

    def transaction(self):
        return _FakeTx()

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))

    async def executemany(self, sql, args):
        self.inserted.append((" ".join(sql.split()), list(args)))


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _FakeAcquire(self.conn)


def _rows(tmp_path):
    (tmp_path / "AssignmentScheduler.cs").write_text(CS_SRC, encoding="utf-8")
    return indexer.collect_symbols_sync(str(tmp_path), IndexingConfig(), None, None)


def test_a_csharp_file_produces_definition_rows(tmp_path):
    rows = _rows(tmp_path)
    defs = {r["name"]: r for r in rows if r["kind"] == "def"}
    assert defs["AssignmentScheduler"]["node_type"] == "class_declaration"
    assert defs["AvailableSlots"]["node_type"] == "method_declaration"
    assert defs["AvailableSlots"]["file_path"] == "AssignmentScheduler.cs"


def test_a_csharp_file_produces_reference_rows(tmp_path):
    rows = _rows(tmp_path)
    refs = {r["name"] for r in rows if r["kind"] == "ref"}
    assert "ComputeAvailableSlots" in refs
    assert "SchedulerService" in refs


def test_repeated_names_become_occurrences(tmp_path):
    rows = _rows(tmp_path)
    operator_code = [r for r in rows if r["name"] == "operatorCode"]
    assert len(operator_code) == 1
    assert operator_code[0]["occurrences"] >= 2


def test_rows_reach_the_insert_with_org_and_project(tmp_path):
    rows = _rows(tmp_path)
    conn = _FakeConn()
    asyncio.run(indexer._store_symbols(_FakePool(conn), 1, 18, "scheduling", rows))

    assert "DELETE FROM repo_symbols" in conn.executed[0][0]
    assert conn.executed[0][1] == (18, "scheduling")

    sql, params = conn.inserted[0]
    assert "INSERT INTO repo_symbols" in sql
    by_name = {p[4]: p for p in params}
    assert by_name["AssignmentScheduler"][:4] == (1, 18, "scheduling", "AssignmentScheduler.cs")
    assert by_name["AssignmentScheduler"][5] == "def"
    assert by_name["AssignmentScheduler"][6] == "class_declaration"
```

- [ ] **Step 2: Run the test to verify it fails**

Before running, confirm you are on a tree where Tasks 1–6 have landed. Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_symbol_rows_persist.py
```

Expected: PASS. This test asserts the behaviour Tasks 1–6 built; it is a regression guard, not a new feature. If it fails, the failure is a real defect in Tasks 1–6 — fix that, do not weaken the test.

- [ ] **Step 3: Update the README architecture sentence**

In `README.md` at the repository root, line 31 currently begins:

```
Indexing uses tree-sitter (Python, JS/TS, Go, Java), with scheduled re-indexing via APScheduler.
```

Replace that opening clause so the sentence reads:

```
Indexing uses tree-sitter (Python, JS/TS, Go, Java, C#, PHP, Kotlin, Rust), with scheduled re-indexing via APScheduler.
```

Leave the rest of the line (`Re-indexing is **incremental**: …`) unchanged.

- [ ] **Step 4: Update the README code-intelligence bullet**

In `README.md`, line 10 currently reads:

```
- **Code intelligence** — a symbol graph (definitions, references, imports) built at index time powers `repo_map` (ranked file overview for a query, definitions first), `repo_neighbors` (callers, callees, and related files) and `repo_symbols`; `repo_get_file` reads by line window so agents pull only the lines they need.
```

Replace it with:

```
- **Code intelligence** — a symbol graph (definitions, references, imports) built at index time powers `repo_map` (ranked file overview for a query, definitions first), `repo_neighbors` (callers, callees, and related files) and `repo_symbols`; `repo_get_file` reads by line window so agents pull only the lines they need. Symbols come from tree-sitter for Python, JavaScript/TypeScript, Go, Java, C#, PHP, Kotlin and Rust, and from a regex extractor for SQL (`CREATE TABLE`/`VIEW`/`MATERIALIZED VIEW`/`FUNCTION`/`PROCEDURE`/`TRIGGER`/`INDEX`/`TYPE`, definitions only).
```

No other README section changes: this feature adds no MCP tool, permission or environment variable.

- [ ] **Step 5: Run the full server suite**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: PASS with no failures and no errors. Report the final summary line. If anything fails, fix it before committing — do not commit a red suite and do not skip tests to make it green.

- [ ] **Step 6: Commit**

```bash
git add services/server/tests/test_symbol_rows_persist.py README.md
git commit -m "cover symbol persistence and document new languages"
```

---

## Ambiguities resolved while writing this plan

- The spec lists per-language *reference* nodes (`invocation_expression`, `function_call_expression`, `call_expression`, …) and *import* nodes (`using_directive`, `namespace_use_declaration`, `import_header`, `use_declaration`), but `symbols.py` has no such tables: references are any identifier leaf (`REF_NODES`), which already covers both calls and imports for C#, Kotlin and Rust. Only PHP needs an addition, because its grammar names every identifier leaf `name`. No reference or import table is introduced; the per-language tests assert that the imported name and the called name both appear in `refs`.
- The spec says `.sql` "moves from `TEXT_EXTENSIONS` to a path that chunks as text". `TEXT_EXTENSIONS` **is** that path, so `.sql` stays where it is; only `_detect_language` and `collect_symbols_sync` change.
- The spec points at "`src/indexer/embedder.py` or wherever language names are validated". There is no such validation anywhere: `embedder.py` never mentions a language, and `RepoConfig.language` (`src/config.py`) is a free-form string whose default `"auto"` is turned into `None` by `index_repo` so that `_detect_language` decides per file. No allow-list needs extending, and no task touches `embedder.py` or `config.py`.
- SQL `kind` for a two-word object type is written with an underscore (`materialized_view`), so kinds stay single tokens like the tree-sitter node types stored in the same column.
- SQL names keep only the last segment of a qualified name (`analytics.operator_load` → `operator_load`) and drop the quoting characters, so they match the bare identifiers that references elsewhere in the repository produce.
- SQL definitions pass through the same `_keep` filter as every other language, so a table called `data` or `ab` is dropped rather than becoming a graph hub.
- The spec's name-field note covers Rust `impl_item` but not Kotlin `property_declaration`, which has no `name` field at all (the name lives in a `variable_declaration` child). A second override table, `NAME_HOLDERS`, handles it; without it every Kotlin `val`/`var` would be dropped.
- `tree-sitter-kotlin` reports a Kotlin `interface` as `class_declaration`, so `interface_declaration` is not in the Kotlin node set and the interface is expected with kind `class_declaration`.
- C# declarations sit three levels deep inside a `namespace` block, past the chunker's hard-coded `depth <= 2`; a per-language `_CHUNK_MAX_DEPTH` gives C# a limit of 3 and leaves every other language on 2.
- `_get_parser` currently imports all grammars inside one `try`, so adding four imports would mean one missing wheel silences Python and Java too. It is replaced by a per-language loader table with a parser cache — also a real saving, since it was rebuilding the parser for every file.
- Grammar availability (verified on PyPI, `pip download` for cp311 against manylinux x86_64, manylinux aarch64 and win_amd64, then loaded with `tree-sitter` 0.26): **all four are available**, none is dropped. `tree-sitter-c-sharp` 0.23.5 (`tree_sitter_c_sharp.language()`, ABI 15), `tree-sitter-php` 0.24.1 (`tree_sitter_php.language_php()`, ABI 15), `tree-sitter-kotlin` 1.1.0 (`tree_sitter_kotlin.language()`, ABI 14), `tree-sitter-rust` 0.24.2 (`tree_sitter_rust.language()`, ABI 15). All ship `abi3` wheels, so no build step and no Dockerfile change.
