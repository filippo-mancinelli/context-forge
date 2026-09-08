# More languages in the symbol graph — design

## Problem

Parsing and the symbol graph cover Python, JavaScript, TypeScript, Go and
Java (`PARSEABLE_EXTENSIONS` in `src/indexer/indexer.py`, `DEF_NODES` and the
reference/import node tables in `src/indexer/symbols.py`). Everything else is
indexed as plain text with no symbols.

## Design

### Languages

| Language | Extensions | Grammar package (PyPI) | Definition nodes |
|---|---|---|---|
| C# | `.cs` | `tree-sitter-c-sharp` | `class_declaration`, `interface_declaration`, `struct_declaration`, `record_declaration`, `enum_declaration`, `method_declaration`, `constructor_declaration`, `property_declaration` |
| PHP | `.php` | `tree-sitter-php` (use the `php` language, not `php_only`) | `class_declaration`, `interface_declaration`, `trait_declaration`, `enum_declaration`, `function_definition`, `method_declaration` |
| Kotlin | `.kt`, `.kts` | `tree-sitter-kotlin` | `class_declaration`, `object_declaration`, `function_declaration`, `property_declaration` |
| Rust | `.rs` | `tree-sitter-rust` | `function_item`, `struct_item`, `enum_item`, `trait_item`, `impl_item`, `type_item`, `mod_item` |
| SQL | `.sql` | none (see below) | regex extractor |

The implementer verifies each package installs on Python 3.11 wheels
(`uv pip install` in the venv; check the Docker base image can build it). If a
grammar is unavailable, that language is dropped from this feature with a
ledger note; nothing else is substituted.

SQL: no maintained grammar with reliable wheels. `src/indexer/symbols.py` gets
`extract_sql_symbols(content) -> dict` using regexes for
`CREATE [OR REPLACE] (TABLE|VIEW|MATERIALIZED VIEW|FUNCTION|PROCEDURE|TRIGGER|INDEX|TYPE) [IF NOT EXISTS] <name>`
(case-insensitive, optional schema prefix, quoted identifiers), producing
definitions only (kind = the object type, lowercased). References for SQL
are not extracted.

### Integration points

- `src/indexer/indexer.py`: extend `PARSEABLE_EXTENSIONS`, the extension →
  language map, and parser creation (whatever `_get_parser`/language loading
  does today) for the four grammars; `.sql` moves from `TEXT_EXTENSIONS` to a
  path that chunks as text but runs the SQL symbol extractor.
- `src/indexer/symbols.py`: `DEF_NODES`, the name-field lookup per node type
  (the field is `name` for all listed nodes except Rust `impl_item`, whose name
  is the `type` field, and PHP `method_declaration`/`function_definition`,
  which use `name`), reference nodes (`call_expression` / `invocation_expression`
  for C#, `function_call_expression` and `member_call_expression` for PHP,
  `call_expression` for Kotlin and Rust) and import nodes (`using_directive`,
  `namespace_use_declaration`, `import_header`, `use_declaration`).
- Chunking: if the indexer splits parseable files on definition nodes, the new
  node sets must feed that split too (same table, one source of truth).
- `src/indexer/embedder.py` or wherever language names are validated: add the
  new names.
- `pyproject.toml`: new grammar dependencies; Dockerfile only if a build step
  is required.

### Tests

One test module per language with a 20–40 line snippet asserting the extracted
definitions (name, kind, line) and at least one reference and one import; a
SQL test with tables, a view, a function and a quoted identifier; a language
detection test for every new extension; an indexer test proving a `.cs` file
produces symbol rows through the existing symbol persistence path (fake pool).

## README

Architecture sentence lists the languages; symbol graph bullet updated.
