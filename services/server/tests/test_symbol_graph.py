"""Estrazione dei simboli e ranking del grafo del repository.

Nessun database: si verifica che dai sorgenti escano le definizioni e i
riferimenti giusti, e che il ranking metta davanti i file che contano per la
domanda invece dei piu' grossi.
"""
import pytest

from src.indexer import symbols
from src.indexer.indexer import _get_parser


PY_SRC = '''
from services import scheduler_service


class AssignmentScheduler:
    def process_standard_candidates(self, target):
        capacity = scheduler_service.available_slots(target)
        return capacity


def build_scheduler():
    return AssignmentScheduler()
'''

JAVA_SRC = """
public class AssignmentSchedulerService {
    public void processStandardCandidates(String target) {
        OperatorLoadState state = loadOperatorState(target);
        state.availableSlots(maxActiveTicketsPerOperator);
    }
}
"""


def _extract(src, language):
    parser = _get_parser(language)
    if parser is None:
        pytest.skip(f"parser tree-sitter non disponibile per {language}")
    return symbols.extract_symbols(src, language, parser)


# ── estrazione ────────────────────────────────────────────────────────────────

def test_extracts_python_definitions():
    out = _extract(PY_SRC, "python")
    names = {d["name"] for d in out["defs"]}
    assert "AssignmentScheduler" in names
    assert "process_standard_candidates" in names
    assert "build_scheduler" in names


def test_extracts_python_references_to_other_files():
    out = _extract(PY_SRC, "python")
    refs = {r["name"] for r in out["refs"]}
    assert "scheduler_service" in refs
    assert "available_slots" in refs


def test_definition_name_is_not_also_a_reference():
    out = _extract(PY_SRC, "python")
    defs_at = {(d["name"], d["line"]) for d in out["defs"]}
    refs_at = {(r["name"], r["line"]) for r in out["refs"]}
    assert not (defs_at & refs_at)


def test_short_and_generic_names_are_dropped():
    out = _extract("def go(x):\n    y = x\n    return y\n", "python")
    assert out["defs"] == []
    names = {r["name"] for r in out["refs"]}
    assert names == set()


def test_extracts_java_definitions_and_references():
    out = _extract(JAVA_SRC, "java")
    names = {d["name"] for d in out["defs"]}
    assert "AssignmentSchedulerService" in names
    assert "processStandardCandidates" in names
    refs = {r["name"] for r in out["refs"]}
    assert "OperatorLoadState" in refs
    assert "availableSlots" in refs


def test_unsupported_language_yields_nothing():
    assert symbols.extract_symbols("qualsiasi cosa", "cobol", None) == {"defs": [], "refs": []}


def test_broken_source_does_not_raise():
    out = _extract("class Rotta(:\n  def", "python")
    assert isinstance(out["defs"], list)


# ── archi ─────────────────────────────────────────────────────────────────────

def test_build_edges_sums_weights_and_drops_self_loops():
    rows = [
        {"src_file": "a.py", "dst_file": "b.py", "weight": 2},
        {"src_file": "a.py", "dst_file": "b.py", "weight": 3},
        {"src_file": "a.py", "dst_file": "a.py", "weight": 9},
    ]
    assert symbols.build_edges(rows) == {("a.py", "b.py"): 5}


# ── PageRank ──────────────────────────────────────────────────────────────────

def test_pagerank_favours_the_most_referenced_file():
    edges = {
        ("a.py", "core.py"): 1,
        ("b.py", "core.py"): 1,
        ("c.py", "core.py"): 1,
    }
    scores = symbols.pagerank(edges)
    assert max(scores, key=scores.get) == "core.py"
    assert scores["core.py"] > scores["a.py"]


def test_rank_flows_through_to_what_the_hub_depends_on():
    # core.py e' molto referenziato ma dipende a sua volta da util.py: il rango
    # attraversa l'arco, e util.py finisce sopra. E' il comportamento voluto,
    # non un artefatto: cio' da cui dipende un file centrale conta almeno quanto
    # il file centrale.
    edges = {
        ("a.py", "core.py"): 1,
        ("b.py", "core.py"): 1,
        ("c.py", "core.py"): 1,
        ("core.py", "util.py"): 1,
    }
    scores = symbols.pagerank(edges)
    assert scores["util.py"] > scores["core.py"] > scores["a.py"]


def test_pagerank_is_deterministic_and_normalised():
    edges = {("a.py", "b.py"): 1, ("b.py", "c.py"): 2, ("c.py", "a.py"): 1}
    first = symbols.pagerank(edges)
    second = symbols.pagerank(edges)
    assert first == second
    assert abs(sum(first.values()) - 1.0) < 1e-6


def test_personalization_moves_the_ranking():
    edges = {
        ("a.py", "core.py"): 1,
        ("b.py", "core.py"): 1,
        ("c.py", "core.py"): 1,
        ("d.py", "niche.py"): 1,
    }
    plain = symbols.pagerank(edges)
    focused = symbols.pagerank(edges, personalization={"niche.py": 1.0})
    assert plain["core.py"] > plain["niche.py"]
    assert focused["niche.py"] > focused["core.py"]


def test_pagerank_on_an_empty_graph():
    assert symbols.pagerank({}) == {}


# ── query e classifica ────────────────────────────────────────────────────────

def test_query_tokens_keeps_only_plausible_names():
    assert symbols.query_tokens("perche' lo scheduler non assegna availableSlots?") == {
        "perche", "scheduler", "assegna", "availableSlots",
    }
    assert symbols.query_tokens(None) == set()


def test_rank_files_puts_the_queried_symbol_first():
    edge_rows = [
        {"src_file": "app/a.py", "dst_file": "app/core.py", "weight": 5},
        {"src_file": "app/b.py", "dst_file": "app/core.py", "weight": 5},
        {"src_file": "app/c.py", "dst_file": "app/scheduler.py", "weight": 1},
    ]
    def_rows = [
        {"file_path": "app/core.py", "name": "CoreService", "kind": "class_definition"},
        {"file_path": "app/scheduler.py", "name": "availableSlots", "kind": "function_definition"},
    ]
    generic = symbols.rank_files(edge_rows, def_rows)
    assert generic[0]["file_path"] == "app/core.py"

    focused = symbols.rank_files(edge_rows, def_rows, query="perche' availableSlots e' a zero")
    assert focused[0]["file_path"] == "app/scheduler.py"
    assert "availableSlots" in focused[0]["defines"]


def test_rank_files_keeps_an_isolated_file_that_matches_the_query():
    def_rows = [{"file_path": "app/orfano.py", "name": "welfareLookup", "kind": "function_definition"}]
    ranked = symbols.rank_files([], def_rows, query="welfareLookup")
    assert [r["file_path"] for r in ranked] == ["app/orfano.py"]


def test_rank_files_respects_the_limit():
    edge_rows = [
        {"src_file": f"f{i}.py", "dst_file": "core.py", "weight": 1} for i in range(10)
    ]
    assert len(symbols.rank_files(edge_rows, [], limit=3)) == 3


def test_defining_the_name_beats_having_it_in_the_path():
    # Una parola della domanda che compare nel path di venti file non deve
    # coprire l'unico file che quel nome lo definisce davvero.
    edge_rows = [
        {"src_file": f"app/assegnazione/f{i}.java", "dst_file": "app/other.java", "weight": 1}
        for i in range(20)
    ]
    def_rows = [{"file_path": "app/Scheduler.java", "name": "availableSlots", "kind": "method_declaration"}]
    for i in range(20):
        def_rows.append(
            {"file_path": f"app/assegnazione/f{i}.java", "name": f"Filler{i}", "kind": "class_declaration"}
        )
    ranked = symbols.rank_files(
        edge_rows, def_rows, query="assegnazione: availableSlots resta a zero", limit=5
    )
    assert ranked[0]["file_path"] == "app/Scheduler.java"


def test_a_short_query_token_does_not_match_paths():
    def_rows = [{"file_path": "app/assegna/x.java", "name": "Qualcosa", "kind": "class_declaration"}]
    ranked = symbols.rank_files([], def_rows, query="slot")
    assert ranked == []
