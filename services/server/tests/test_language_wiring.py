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
