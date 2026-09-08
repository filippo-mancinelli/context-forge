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
