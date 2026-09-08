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
