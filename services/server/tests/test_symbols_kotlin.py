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
