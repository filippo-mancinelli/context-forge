"""Grafo dei simboli di un repository: definizioni, riferimenti, ranking.

Il retrieval semantico risponde a "dove si parla di X", non a "chi chiama X" ne'
a "da dove conviene partire". Un agente che ha solo quello ricostruisce la
struttura del repo a ogni domanda, un giro di tool alla volta.

Qui si estraggono dai sorgenti due fatti elementari — dove un nome e' definito e
dove viene usato — e da quelli si ricava un grafo fra file, pesato sul numero di
riferimenti condivisi. Il ranking dei file e' un PageRank su quel grafo, con la
personalizzazione sui file che definiscono i nomi cercati: e' la stessa idea
della repo map di Aider, e il vicinato di un simbolo e' l'ego-graph di RepoGraph.

Il modulo e' puro: nessun accesso al database, cosi' estrazione e ranking sono
verificabili da soli.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

# Nodi che valgono come definizione, per linguaggio. Stesso insieme usato per il
# chunking, piu' i costruttori di tipo: qui interessa il nome esportato.
DEF_NODES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "class_declaration", "method_definition"},
    "typescript": {
        "function_declaration",
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "method_definition",
        "enum_declaration",
    },
    "tsx": {"function_declaration", "class_declaration", "method_definition"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "java": {
        "class_declaration",
        "method_declaration",
        "interface_declaration",
        "enum_declaration",
    },
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
    "php": {
        "class_declaration",
        "interface_declaration",
        "trait_declaration",
        "enum_declaration",
        "function_definition",
        "method_declaration",
    },
    "kotlin": {
        "class_declaration",
        "object_declaration",
        "function_declaration",
        "property_declaration",
    },
}

# Nodi che valgono come uso di un nome.
REF_NODES = {
    "identifier",
    "type_identifier",
    "field_identifier",
    "property_identifier",
}

# Nodi di riferimento aggiuntivi, per grammatiche che non usano ``identifier``.
REF_NODES_EXTRA: dict[str, set[str]] = {"php": {"name"}}

# Nodi il cui nome non sta nel campo ``name``: (linguaggio, nodo) -> campo.
NAME_FIELDS: dict[tuple[str, str], str] = {}

# Nodi il cui nome sta dentro un figlio: (linguaggio, nodo) -> tipo del figlio.
NAME_HOLDERS: dict[tuple[str, str], str] = {
    ("kotlin", "property_declaration"): "variable_declaration",
}

# Identificatori usati come ripiego quando il campo del nome manca.
IDENT_NODES = ("identifier", "type_identifier", "property_identifier")

# Nomi troppo comuni per portare informazione: creerebbero archi fra tutti.
STOP_NAMES = {
    "get", "set", "add", "new", "run", "init", "main", "self", "this", "value",
    "data", "name", "id", "type", "key", "item", "list", "map", "result", "error",
    "string", "number", "boolean", "object", "array", "void", "null", "true",
    "false", "length", "index", "args", "kwargs", "params", "options", "config",
}

MIN_NAME_LEN = 4

# Pesi della ripartenza: definire il nome cercato conta molto piu' che averlo
# nel path, e un token corto sul path non conta affatto.
DEF_MATCH_WEIGHT = 5.0
PATH_MATCH_WEIGHT = 1.0
MIN_PATH_TOKEN_LEN = 6


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


def _keep(name: Optional[str]) -> bool:
    return bool(name) and len(name) >= MIN_NAME_LEN and name.lower() not in STOP_NAMES


def extract_symbols(content: str, language: str, parser: Any) -> dict[str, list[dict]]:
    """Definizioni e riferimenti di un file sorgente.

    ``parser`` e' un parser tree-sitter gia' costruito per il linguaggio (None
    quando il linguaggio non e' supportato: in quel caso non si estrae nulla).
    I riferimenti includono anche i nomi definiti altrove nello stesso file: la
    potatura contro l'insieme delle definizioni del repository avviene a monte
    dell'inserimento, dove quell'insieme e' noto.
    """
    if parser is None or language not in DEF_NODES:
        return {"defs": [], "refs": []}

    source = content.encode("utf-8", "replace")
    try:
        tree = parser.parse(source)
    except Exception:  # noqa: BLE001 - un file illeggibile non deve fermare l'indicizzazione
        return {"defs": [], "refs": []}

    def_nodes = DEF_NODES[language]
    ref_nodes = _ref_nodes(language)
    defs: list[dict] = []
    refs: list[dict] = []
    def_name_spans: set[tuple[int, int]] = set()

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
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
            span = (node.start_byte, node.end_byte)
            if span not in def_name_spans:
                name = source[node.start_byte : node.end_byte].decode("utf-8", "replace")
                if _keep(name):
                    refs.append({"name": name, "line": node.start_point[0] + 1})
        stack.extend(node.children)

    # Il nome di una definizione visitata dopo il suo identifier sarebbe finito
    # anche fra i riferimenti: si toglie qui, a insieme completo.
    declared = {d["name"] for d in defs}
    refs = [
        r
        for r in refs
        if not (r["name"] in declared and any(d["line"] == r["line"] for d in defs))
    ]
    return {"defs": defs, "refs": _dedupe(refs)}


def _dedupe(refs: list[dict]) -> list[dict]:
    seen: set[tuple[str, int]] = set()
    out: list[dict] = []
    for r in refs:
        key = (r["name"], r["line"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def build_edges(rows: Iterable[dict]) -> dict[tuple[str, str], int]:
    """Archi file→file dal join fra riferimenti e definizioni.

    ``rows`` sono record ``{src_file, dst_file, weight}`` gia' accoppiati per
    nome: la funzione somma i pesi e scarta gli auto-anelli, che non dicono
    nulla su quali file dipendono da quali.
    """
    edges: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        src, dst = row["src_file"], row["dst_file"]
        if src == dst:
            continue
        edges[(src, dst)] += int(row.get("weight") or 1)
    return dict(edges)


def pagerank(
    edges: dict[tuple[str, str], int],
    personalization: Optional[dict[str, float]] = None,
    damping: float = 0.85,
    iterations: int = 30,
    extra_nodes: Optional[Iterable[str]] = None,
) -> dict[str, float]:
    """PageRank sui file, con pesi sugli archi e ripartenza personalizzata.

    La personalizzazione e' il punto: senza, si ottiene la classifica dei file
    piu' centrali del repository, sempre la stessa; con i file che definiscono i
    nomi della domanda, si ottiene la classifica rilevante per quella domanda.
    """
    nodes: set[str] = set(extra_nodes or ())
    for src, dst in edges:
        nodes.add(src)
        nodes.add(dst)
    if not nodes:
        return {}

    out_weight: dict[str, float] = defaultdict(float)
    incoming: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (src, dst), weight in edges.items():
        out_weight[src] += weight
        incoming[dst].append((src, float(weight)))

    n = len(nodes)
    if personalization:
        total = sum(v for k, v in personalization.items() if k in nodes and v > 0)
    else:
        total = 0.0
    if total > 0:
        restart = {
            node: (personalization.get(node, 0.0) / total if personalization.get(node, 0.0) > 0 else 0.0)
            for node in nodes
        }
    else:
        restart = {node: 1.0 / n for node in nodes}

    rank = {node: 1.0 / n for node in nodes}
    for _ in range(iterations):
        dangling = sum(rank[node] for node in nodes if out_weight[node] == 0)
        nxt = {}
        for node in nodes:
            incoming_mass = sum(
                rank[src] * weight / out_weight[src]
                for src, weight in incoming.get(node, ())
                if out_weight[src] > 0
            )
            nxt[node] = (1.0 - damping) * restart[node] + damping * (
                incoming_mass + dangling * restart[node]
            )
        total_rank = sum(nxt.values()) or 1.0
        rank = {node: value / total_rank for node, value in nxt.items()}
    return rank


def query_tokens(query: Optional[str]) -> set[str]:
    """Nomi plausibili estratti da una domanda in linguaggio naturale."""
    if not query:
        return set()
    out: set[str] = set()
    token = []
    for ch in query:
        if ch.isalnum() or ch == "_":
            token.append(ch)
        else:
            if token:
                out.add("".join(token))
                token = []
    if token:
        out.add("".join(token))
    return {t for t in out if _keep(t)}


def rank_files(
    edge_rows: Iterable[dict],
    definition_rows: Iterable[dict],
    query: Optional[str] = None,
    limit: int = 25,
) -> list[dict]:
    """Classifica dei file piu' rilevanti, con i simboli che ciascuno definisce.

    ``definition_rows`` sono record ``{file_path, name, kind}``; servono sia per
    la personalizzazione sia per dire, di ogni file in classifica, che cosa ci
    si trova dentro.
    """
    edges = build_edges(edge_rows)
    defs_by_file: dict[str, list[dict]] = defaultdict(list)
    for row in definition_rows:
        defs_by_file[row["file_path"]].append({"name": row["name"], "kind": row.get("kind")})

    tokens = {t.lower() for t in query_tokens(query)}
    personalization: dict[str, float] = {}
    direct: set[str] = set()
    if tokens:
        for file_path, defs in defs_by_file.items():
            # Definire un nome della domanda e' un segnale forte; averlo nel path
            # e' un indizio, e solo per token abbastanza lunghi: una parola
            # italiana corta ("assegna") pesca decine di file per caso.
            def_hits = sum(1 for d in defs if d["name"].lower() in tokens)
            if def_hits:
                direct.add(file_path)
            hits = DEF_MATCH_WEIGHT * def_hits
            lowered = file_path.lower()
            hits += PATH_MATCH_WEIGHT * sum(
                1 for t in tokens if len(t) >= MIN_PATH_TOKEN_LEN and t in lowered
            )
            if hits:
                personalization[file_path] = float(hits)

    # Un file senza archi ma che definisce un nome cercato deve comunque entrare
    # in classifica: e' spesso proprio quello da leggere.
    scores = pagerank(
        edges, personalization or None, extra_nodes=personalization.keys() or None
    )

    # I file che definiscono davvero un nome della domanda vanno in testa: fra
    # loro decide il grafo, ma nessun hub li puo' scavalcare.
    ordered = sorted(
        scores.items(), key=lambda kv: (0 if kv[0] in direct else 1, -kv[1], kv[0])
    )[:limit]
    return [
        {
            "file_path": file_path,
            "score": round(score, 6),
            "defines": [d["name"] for d in defs_by_file.get(file_path, [])][:12],
        }
        for file_path, score in ordered
    ]
