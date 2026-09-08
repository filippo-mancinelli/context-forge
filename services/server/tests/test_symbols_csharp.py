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
