"""SQL safety validator for read-only execution against external databases.

Ported and generalized from an internal SQL-agent validator: allows a single
SELECT / WITH / SHOW / DESCRIBE / EXPLAIN statement, blocks every mutating or
session-altering keyword, and injects a LIMIT when missing. Validation is
deliberately conservative: a blocked keyword inside a string literal rejects
the query rather than risking a false negative.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_BLOCKED_KEYWORDS = [
    "DROP",
    "DELETE",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "INSERT",
    "UPDATE",
    "REPLACE",
    "RENAME",
    "GRANT",
    "REVOKE",
    "LOCK",
    "UNLOCK",
    "CALL",
    "LOAD",
    "EXEC",
    "EXECUTE",
    "MERGE",
    "COPY",
    "VACUUM",
    "ANALYZE",
    "REINDEX",
    "CLUSTER",
    "PRAGMA",
    "ATTACH",
    "DETACH",
    "PREPARE",
    "DEALLOCATE",
    "HANDLER",
    "DO",
    "LISTEN",
    "NOTIFY",
    "REFRESH",
    "INTO OUTFILE",
    "INTO DUMPFILE",
]

_BLOCKED_PATTERN = re.compile(
    r"(?:^|[\s;(])(" + "|".join(re.escape(k) for k in _BLOCKED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
# `SET var = ...` needs its own pattern: plain \bSET\b would reject ORDER BY ... OFFSET
# and column names like "settings"; we only care about statement-leading SET.
_SET_PATTERN = re.compile(r"(?:^|;)\s*SET\b", re.IGNORECASE)

# SELECT ... INTO scrive: su PostgreSQL crea una tabella, su MySQL assegna
# variabili di sessione. INTO OUTFILE e INTO DUMPFILE restano tra le parole
# bloccate, con il loro messaggio.
_INTO_PATTERN = re.compile(r"\bINTO\b", re.IGNORECASE)

_LIMIT_PATTERN = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_SELECT_PATTERN = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_INFO_PATTERN = re.compile(r"^\s*(SHOW|DESCRIBE|DESC|EXPLAIN)\b", re.IGNORECASE)
_COMMENT_PATTERN = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.DOTALL)


class QueryValidationError(Exception):
    """Raised when a SQL query fails read-only validation."""


class ScopeReferenceError(QueryValidationError):
    """La query nomina uno schema o un database fuori dal perimetro."""

    def __init__(self, reference: str, allowed_schema: str):
        super().__init__(
            f"'{reference}' is outside the scope '{allowed_schema}' of this data source."
        )
        self.reference = reference
        self.allowed_schema = allowed_schema


def validate_query(sql: str, max_rows: int = 100, allowed_schema: Optional[str] = None) -> str:
    """Validate a query for safe read-only execution and return the sanitized SQL.

    Raises QueryValidationError for anything that is not a single read-only
    statement or that reads the system catalogs; with ``allowed_schema`` also
    for any qualified name outside that schema (``ScopeReferenceError``).
    SELECT/WITH statements without a LIMIT get ``LIMIT max_rows`` appended.
    """
    if not sql or not sql.strip():
        raise QueryValidationError("Empty query.")

    # Strip comments so keywords can't be smuggled past (or hidden by) them.
    cleaned = _COMMENT_PATTERN.sub(" ", sql).strip().rstrip(";").strip()
    if not cleaned:
        raise QueryValidationError("Empty query.")

    if ";" in cleaned:
        raise QueryValidationError("Multiple statements are not allowed.")

    match = _BLOCKED_PATTERN.search(cleaned)
    if match:
        raise QueryValidationError(
            f"Query contains blocked operation: {match.group(1).upper()}. "
            "Only read-only statements are allowed."
        )
    if _SET_PATTERN.search(cleaned):
        raise QueryValidationError(
            "SET statements are not allowed. Only read-only statements are allowed."
        )

    is_select = bool(_SELECT_PATTERN.match(cleaned))
    is_info = bool(_INFO_PATTERN.match(cleaned))
    if not is_select and not is_info:
        raise QueryValidationError(
            "Only SELECT, WITH...SELECT, SHOW, DESCRIBE, and EXPLAIN statements are allowed."
        )

    # Vale per ogni istruzione di lettura, con o senza perimetro. Stringhe e
    # identificatori tra virgolette non contano: 'x INTO y', "into" e
    # into_date non sono la parola chiave. Prova entrambe le letture del
    # backslash nelle stringhe: un backslash finale non gestito allo stesso
    # modo dal database farebbe sparire l'INTO dentro una falsa stringa.
    for _string_pattern in _SINGLE_QUOTED_STRING_PATTERNS:
        if _INTO_PATTERN.search(_strip_quoted(cleaned, _string_pattern)):
            raise QueryValidationError(
                "SELECT ... INTO is not allowed: it writes a table or session variables. "
                "Only read-only statements are allowed."
            )

    _check_references(cleaned, allowed_schema)

    if is_select and not _LIMIT_PATTERN.search(cleaned):
        cleaned = f"{cleaned} LIMIT {max_rows}"

    return cleaned


def is_safe_query(sql: str) -> bool:
    try:
        validate_query(sql)
        return True
    except QueryValidationError:
        return False


_WRITE_STATEMENT_PATTERN = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
_WHERE_PATTERN = re.compile(r"\bWHERE\b", re.IGNORECASE)
# Una stringa a apici singoli si legge in due modi secondo il dialetto: con il
# backslash come escape (MySQL, e PostgreSQL con standard_conforming_strings=off)
# o con il backslash come carattere qualunque (PostgreSQL con
# standard_conforming_strings=on, il default): un backslash appena prima
# dell'apice chiude la stringa in un modo e non nell'altro. I controlli che
# leggono i nomi (cataloghi, perimetro, SELECT ... INTO) provano entrambe le
# letture e rifiutano se una sola trova un problema, perché non sanno a priori
# su quale dialetto gira la query.
_SINGLE_QUOTED_STRING_ESCAPE_PATTERN = re.compile(r"'(?:[^'\\]|\\.|'')*'", re.DOTALL)
_SINGLE_QUOTED_STRING_PLAIN_PATTERN = re.compile(r"'(?:[^']|'')*'", re.DOTALL)
_SINGLE_QUOTED_STRING_PATTERNS = (
    _SINGLE_QUOTED_STRING_ESCAPE_PATTERN,
    _SINGLE_QUOTED_STRING_PLAIN_PATTERN,
)
_DOUBLE_QUOTED_IDENTIFIER_PATTERN = re.compile(r'"(?:[^"]|"")*"')
_BACKTICK_QUOTED_IDENTIFIER_PATTERN = re.compile(r"`(?:[^`]|``)*`")
_DOLLAR_QUOTED_STRING_PATTERN = re.compile(r"\$(\w*)\$.*?\$\1\$", re.DOTALL)
_PARENTHESIZED_GROUP_PATTERN = re.compile(r"\([^()]*\)")
# Blocklist per il percorso write: tutto quello del read-only tranne i tre DML ammessi.
_WRITE_BLOCKED_KEYWORDS = [k for k in _BLOCKED_KEYWORDS if k not in ("INSERT", "UPDATE", "DELETE")]
_WRITE_BLOCKED_PATTERN = re.compile(
    r"(?:^|[\s;(])(" + "|".join(re.escape(k) for k in _WRITE_BLOCKED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def _strip_quoted(
    cleaned: str, string_pattern: re.Pattern = _SINGLE_QUOTED_STRING_ESCAPE_PATTERN
) -> str:
    """Toglie stringhe, stringhe dollar-quoted e identificatori tra virgolette
    doppie o backtick: le parole che contengono non sono parole chiave.

    ``string_pattern`` sceglie la lettura del backslash nelle stringhe a apici
    singoli; i chiamanti che devono restare fail-closed sulle due letture
    (cataloghi, perimetro, SELECT ... INTO) chiamano questa funzione una volta
    per ciascun pattern di ``_SINGLE_QUOTED_STRING_PATTERNS``.
    """
    skeleton = string_pattern.sub(" ", cleaned)
    skeleton = _DOUBLE_QUOTED_IDENTIFIER_PATTERN.sub(" ", skeleton)
    skeleton = _BACKTICK_QUOTED_IDENTIFIER_PATTERN.sub(" ", skeleton)
    return _DOLLAR_QUOTED_STRING_PATTERN.sub(" ", skeleton)


def _strip_where_decoys(cleaned: str) -> str:
    """Strip the parts of a statement that can carry a fake WHERE token.

    Used only to build a skeleton for the WHERE-presence check; the returned
    SQL and every other check in ``validate_write_query`` keep operating on
    the original ``cleaned`` string.
    """
    skeleton = _strip_quoted(cleaned)
    while True:
        stripped = _PARENTHESIZED_GROUP_PATTERN.sub(" ", skeleton)
        if stripped == skeleton:
            break
        skeleton = stripped
    return skeleton


def validate_write_query(sql: str, allowed_schema: Optional[str] = None) -> str:
    """Validate a single INSERT/UPDATE/DELETE statement for guarded execution.

    UPDATE and DELETE must carry a WHERE clause; DDL, GRANT, TRUNCATE, session
    statements, multiple statements and the system catalogs are rejected, and
    with ``allowed_schema`` so is any qualified name outside that schema.
    """
    if not sql or not sql.strip():
        raise QueryValidationError("Empty query.")

    cleaned = _COMMENT_PATTERN.sub(" ", sql).strip().rstrip(";").strip()
    if not cleaned:
        raise QueryValidationError("Empty query.")
    if ";" in cleaned:
        raise QueryValidationError("Multiple statements are not allowed.")

    match = _WRITE_BLOCKED_PATTERN.search(cleaned)
    if match:
        raise QueryValidationError(
            f"Query contains blocked operation: {match.group(1).upper()}."
        )
    if _SET_PATTERN.search(cleaned):
        raise QueryValidationError("SET statements are not allowed.")

    stmt = _WRITE_STATEMENT_PATTERN.match(cleaned)
    if not stmt:
        raise QueryValidationError(
            "Only single INSERT, UPDATE or DELETE statements are allowed."
        )
    verb = stmt.group(1).upper()
    if verb in ("UPDATE", "DELETE") and not _WHERE_PATTERN.search(_strip_where_decoys(cleaned)):
        raise QueryValidationError(
            f"{verb} without a WHERE clause is not allowed."
        )
    _check_references(cleaned, allowed_schema)
    return cleaned


# --------------------------------------------------------------------------- #
# Riferimenti fuori perimetro
# --------------------------------------------------------------------------- #
# Controllo euristico, non una prova. Legge la query come sequenza di token
# (stringhe, identificatori tra virgolette doppie o backtick, parole, punti,
# virgole, parentesi) senza un parser SQL completo, e guarda i nomi
# qualificati: quelli in posizione di relazione (dopo FROM, JOIN, UPDATE,
# INTO, TABLE, ONLY, USING, dopo una virgola nella lista del FROM, o come
# oggetto di DESCRIBE/EXPLAIN) e le chiamate di funzione devono avere come
# prefisso lo schema consentito; gli altri nomi qualificati, cioè le colonne,
# possono avere come prefisso anche un nome dichiarato nella query (tabella,
# alias, CTE), perché il motore li risolve comunque sulle voci del FROM.
# Limiti noti:
#  - i nomi dentro le stringhe non si vedono: nextval('altro.seq'),
#    to_regclass('altro.t'), dblink e SQL dinamico restano possibili;
#  - le funzioni di sistema non qualificate (current_schemas(), pg_get_*,
#    version()) restano chiamabili, e una vista o una funzione dello schema
#    del perimetro che legge altri schemi non si vede;
#  - un nome a tre parti database.schema.tabella su PostgreSQL è rifiutato
#    anche quando è corretto, così come una funzione o un tipo qualificati con
#    un altro schema anche se innocui (un'estensione installata in public);
#  - i nomi senza virgolette si confrontano senza distinguere le maiuscole:
#    due schemi che differiscono solo per le maiuscole non si distinguono;
#  - una tabella utente il cui nome comincia per pg_ è rifiutata come catalogo;
#  - un identificatore con caratteri non ASCII (per esempio "acquistì") non è
#    riconosciuto come parola dal tokenizzatore e sfugge al controllo.
# Un identificatore con escape Unicode, U&"..." con l'eventuale coda
# UESCAPE 'c', è un solo token e viene rifiutato in blocco: il nome che il
# server risolve non è quello scritto, quindi confrontarlo con il perimetro o
# con i nomi dei cataloghi leggerebbe un nome diverso da quello eseguito.
# Nessuna lettura dentro il perimetro ha bisogno di quella forma. Una stringa
# con escape Unicode, U&'...', resta ammessa: è un dato, non un nome, e come
# ogni stringa il suo contenuto non si vede (vedi il primo limite qui sopra).
# Su MySQL "u&" seguito da virgolette doppie sarebbe un and bit a bit contro
# una stringa: quella forma viene rifiutata anche lì, un falso positivo raro e
# innocuo, perché il senso del prefisso dipende dal dialetto.
# Il controllo dei riferimenti e quello su SELECT ... INTO leggono la query
# due volte, una per ciascuna lettura del backslash nelle stringhe a apici
# singoli (`_SINGLE_QUOTED_STRING_PATTERNS`), e rifiutano se una sola lettura
# trova un problema: senza questo, un backslash finale in una stringa lascia
# leggere la query in un modo che nasconde un catalogo, un riferimento fuori
# perimetro o un INTO, mentre il database la esegue nell'altro modo.
# La garanzia forte resta nei privilegi dell'utenza sul server remoto.

_BLOCKED_CATALOGS = frozenset({"information_schema", "pg_catalog"})
_CATALOG_RELATION_PREFIX = "pg_"

_TOKEN_KINDS = ("uident", "literal", "dquoted", "bquoted", "word", "punct", "space", "other")

# La coda opzionale UESCAPE 'c' cambia il carattere di escape di un letterale
# con escape Unicode: fa parte del letterale, non è una parola a sé.
_UESCAPE_TAIL = r"(?:\s*[uU][eE][sS][cC][aA][pP][eE]\s*'[^']')?"


def _build_token_pattern(single_quoted_string: str) -> re.Pattern:
    """Un pattern di tokenizzazione, parametrico sulla lettura del backslash
    nelle stringhe a apici singoli (vedi ``_SINGLE_QUOTED_STRING_PATTERNS``)."""
    return re.compile(
        r'(?P<uident>[uU]&"(?:[^"]|"")*"' + _UESCAPE_TAIL + r")"
        r"|(?P<literal>\$(?P<dollartag>\w*)\$.*?\$(?P=dollartag)\$"
        r"|[uU]&" + single_quoted_string + _UESCAPE_TAIL +
        r"|" + single_quoted_string + r"|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)"
        r'|(?P<dquoted>"(?:[^"]|"")*")'
        r"|(?P<bquoted>`(?:[^`]|``)*`)"
        r"|(?P<word>[A-Za-z_][A-Za-z0-9_$]*)"
        r"|(?P<punct>[.,()*])"
        r"|(?P<space>\s+)"
        r"|(?P<other>.)",
        re.DOTALL,
    )


_TOKEN_PATTERN_ESCAPE = _build_token_pattern(_SINGLE_QUOTED_STRING_ESCAPE_PATTERN.pattern)
_TOKEN_PATTERN_PLAIN = _build_token_pattern(_SINGLE_QUOTED_STRING_PLAIN_PATTERN.pattern)
_TOKEN_PATTERNS = (_TOKEN_PATTERN_ESCAPE, _TOKEN_PATTERN_PLAIN)

# Parole dopo le quali viene il nome di una relazione.
_RELATION_LEADERS = frozenset({
    "FROM", "JOIN", "UPDATE", "INTO", "TABLE", "ONLY", "USING", "LATERAL",
})
# Parole che, in testa all'istruzione, sono seguite dal nome di una relazione.
_STATEMENT_RELATION_LEADERS = frozenset({"DESCRIBE", "DESC", "EXPLAIN"})
# Parole che aprono una clausola: servono a sapere se una virgola separa relazioni.
_CLAUSE_KEYWORDS = frozenset({
    "SELECT", "FROM", "JOIN", "WHERE", "ON", "USING", "GROUP", "ORDER", "HAVING",
    "LIMIT", "OFFSET", "UNION", "INTERSECT", "EXCEPT", "WINDOW", "SET", "VALUES",
    "RETURNING", "UPDATE", "INTO", "WITH",
})
_RELATION_LIST_CLAUSES = frozenset({"FROM", "UPDATE", "USING"})
# Funzioni la cui grammatica usa FROM come separatore di argomenti e non come
# inizio di una lista di tabelle: la parentesi aperta subito dopo il loro nome
# non è un livello di query. Decisa dal token prima della parentesi.
_FROM_ARG_FUNCTIONS = frozenset({"EXTRACT", "SUBSTRING", "TRIM", "OVERLAY", "POSITION"})
_SHOW_TABLE_OBJECTS = frozenset({"COLUMNS", "FIELDS", "INDEX", "INDEXES", "KEYS"})
# Forme di SHOW ammesse, riconosciute dalle parole iniziali: parlano degli
# oggetti del database o dello schema su cui la connessione è aperta. Tutto il
# resto di SHOW legge il server, non il perimetro: i grant dell'utenza elencano
# ogni database raggiungibile, PROCESSLIST e ENGINE INNODB STATUS mostrano le
# query delle altre sessioni con i loro letterali, VARIABLES e STATUS la
# configurazione. L'elenco è una lista di ammessi e vale su ogni perimetro, come
# le regole sui cataloghi di sistema: una forma nuova di SHOW è rifiutata finché
# non la si aggiunge qui. Il confronto del bersaglio di FROM/IN con lo schema
# consentito, invece, vale solo dove un perimetro c'è.
# SHOW CREATE TABLE e SHOW CREATE VIEW stanno qui perché parlano di un oggetto
# del perimetro, ma non arrivano fin qui: CREATE è tra le parole bloccate e la
# query viene rifiutata prima, con il messaggio della parola bloccata. Non è una
# dimenticanza: la lista dice quali forme di SHOW riguardano il perimetro, e
# quella regola è un'altra.
_ALLOWED_SHOW_FORMS = (
    ("TABLES",),
    ("FULL", "TABLES"),
    ("COLUMNS",),
    ("FULL", "COLUMNS"),
    ("FIELDS",),
    ("FULL", "FIELDS"),
    ("INDEX",),
    ("INDEXES",),
    ("KEYS",),
    ("TABLE", "STATUS"),
    ("TRIGGERS",),
    ("CREATE", "TABLE"),
    ("CREATE", "VIEW"),
)


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    quoted: bool = False

    def word(self) -> str:
        """La parola in maiuscolo se il token è una parola nuda, altrimenti ''."""
        return self.value.upper() if self.kind == "name" and not self.quoted else ""


@dataclass
class _Reference:
    parts: list[_Token]
    relation: bool
    function: bool

    @property
    def text(self) -> str:
        return ".".join(p.value for p in self.parts)


def _is_punct(token: Optional[_Token], value: str) -> bool:
    return token is not None and token.kind == "punct" and token.value == value


def _same_name(token: _Token, name: str) -> bool:
    """I nomi tra virgolette si confrontano esatti, quelli nudi senza maiuscole."""
    if token.quoted:
        return token.value == name
    return token.value.lower() == name.lower()


def _tokenize(sql: str, token_pattern: re.Pattern) -> list[_Token]:
    tokens: list[_Token] = []
    for match in token_pattern.finditer(sql):
        kind = next(k for k in _TOKEN_KINDS if match.group(k) is not None)
        text = match.group(kind)
        if kind == "space":
            continue
        if kind == "dquoted":
            tokens.append(_Token("name", text[1:-1].replace('""', '"'), True))
        elif kind == "bquoted":
            tokens.append(_Token("name", text[1:-1].replace("``", "`"), True))
        elif kind == "word":
            tokens.append(_Token("name", text))
        else:
            tokens.append(_Token(kind, text))
    return tokens


def _contexts(tokens: list[_Token]) -> list[tuple[Optional[str], bool, bool]]:
    """Per ogni token: la clausola in corso al suo livello di parentesi, se
    quel livello è una query, e se il token è il primo di una parentesi
    aperta in posizione di relazione (dopo FROM, JOIN, USING, TABLE, ONLY,
    UPDATE, INTO, o una virgola nella lista del FROM; ereditata anche da una
    parentesi annidata subito dentro una parentesi già in quella posizione).
    In quel caso il token conta come se lo precedesse un leader di relazione,
    perché la parentesi apre comunque una lista di tabelle il cui primo
    elemento non ha un leader testuale davanti a sé, solo la parentesi.

    ON (la condizione di un JOIN) e WITH (di "WITH ORDINALITY") non chiudono
    la lista di relazioni in corso: dopo di loro una virgola introduce ancora
    una relazione. USING invece ha due grammatiche diverse: dopo un JOIN è
    l'elenco delle colonne comuni tra parentesi (non relazioni), mentre come
    clausola di DELETE/UPDATE introduce altre relazioni; la si distingue da
    "last_leader", l'ultimo leader di relazione visto a questo livello.

    Ogni parentesi apre un livello di query, tranne quelle di una funzione a
    argomenti separati da FROM (``_FROM_ARG_FUNCTIONS``, per esempio
    EXTRACT), decisa dal token prima della parentesi."""
    frames: list[dict] = [
        {"clause": None, "query": True, "seed": False, "consumed": True, "last_leader": None}
    ]
    out: list[tuple[Optional[str], bool, bool]] = []
    prev: Optional[_Token] = None
    for tok in tokens:
        frame = frames[-1]
        is_first_in_frame = not frame["consumed"]
        relation_seed = frame["seed"] if is_first_in_frame else False
        frame["consumed"] = True
        out.append((frame["clause"], frame["query"], relation_seed))
        word = tok.word()
        if _is_punct(tok, "("):
            leader = prev.word() if prev is not None else ""
            if leader == "USING":
                # Solo la USING di DELETE/UPDATE apre una lista di relazioni:
                # quella di un JOIN apre un elenco di colonne (vedi sopra).
                direct_leader = frame["last_leader"] == "USING"
            else:
                direct_leader = leader in _RELATION_LEADERS
            direct_leader = direct_leader or (
                _is_punct(prev, ",") and frame["clause"] in _RELATION_LIST_CLAUSES
            )
            is_relation_paren = direct_leader or (is_first_in_frame and frame["seed"])
            frames.append({
                "clause": "FROM" if is_relation_paren else None,
                "query": leader not in _FROM_ARG_FUNCTIONS,
                "seed": is_relation_paren,
                "consumed": False,
                "last_leader": "FROM" if is_relation_paren else None,
            })
        elif _is_punct(tok, ")"):
            if len(frames) > 1:
                frames.pop()
        elif _is_punct(tok, ","):
            if frame["clause"] in _RELATION_LIST_CLAUSES:
                frame["last_leader"] = frame["clause"]
        elif word == "JOIN":
            frame["clause"] = "FROM"
            frame["last_leader"] = "JOIN"
        elif word == "ON" or word == "WITH":
            pass
        elif word == "USING":
            if frame["last_leader"] == "JOIN":
                frame["last_leader"] = "USING_JOIN"
            else:
                frame["clause"] = "USING"
                frame["last_leader"] = "USING"
        elif word in _CLAUSE_KEYWORDS:
            frame["clause"] = word
            frame["last_leader"] = word if word in _RELATION_LEADERS else None
        prev = tok
    return out


def _alias_after(tokens: list[_Token], index: int) -> Optional[str]:
    if index < len(tokens) and tokens[index].word() == "AS":
        index += 1
    if index < len(tokens) and tokens[index].kind == "name":
        return tokens[index].value.lower()
    return None


def _references(tokens: list[_Token]) -> tuple[list[_Reference], set[str]]:
    """Nomi (qualificati o no) con la loro posizione, e nomi dichiarati nella query.

    Dichiarare un nome in più allarga soltanto i prefissi ammessi per le
    colonne, che il motore risolve comunque sulle voci del FROM: per questo la
    raccolta dei nomi dichiarati è generosa.
    """
    contexts = _contexts(tokens)
    references: list[_Reference] = []
    declared: set[str] = set()
    count = len(tokens)
    i = 0
    while i < count:
        tok = tokens[i]
        if tok.kind != "name":
            i += 1
            continue
        parts = [tok]
        j = i
        while j + 2 < count and _is_punct(tokens[j + 1], ".") and (
            tokens[j + 2].kind == "name" or _is_punct(tokens[j + 2], "*")
        ):
            parts.append(tokens[j + 2])
            j += 2
            if _is_punct(tokens[j], "*"):
                break
        prev = tokens[i - 1] if i > 0 else None
        clause, in_query, relation_seed = contexts[i]
        leader = prev is not None and prev.word() in _RELATION_LEADERS
        listed = _is_punct(prev, ",") and clause in _RELATION_LIST_CLAUSES
        first = i == 1 and prev is not None and prev.word() in _STATEMENT_RELATION_LEADERS
        # relation_seed: primo nome dentro una parentesi aperta in posizione
        # di relazione, senza un leader testuale davanti a sé (solo la "(").
        relation = (in_query and (leader or listed or relation_seed)) or first
        function = j + 1 < count and _is_punct(tokens[j + 1], "(")
        references.append(_Reference(parts, relation, function))
        if relation and not function:
            declared.add(parts[-1].value.lower())
            alias = _alias_after(tokens, j + 1)
            if alias:
                declared.add(alias)
        i = j + 1

    for k, tok in enumerate(tokens):
        nxt = tokens[k + 1] if k + 1 < count else None
        if nxt is None or nxt.kind != "name":
            continue
        if tok.word() == "AS" or _is_punct(tok, ")"):
            # Alias di colonna o di sottoquery: "... AS nome", "(...) nome".
            declared.add(nxt.value.lower())
        elif tok.kind == "name" and nxt.word() == "AS":
            # Nome di una CTE: "nome AS (...)".
            declared.add(tok.value.lower())
    return references, declared


def _catalog_error(reference: str) -> QueryValidationError:
    return QueryValidationError(
        f"'{reference}' reads the system catalogs (information_schema, pg_catalog), "
        "which are not available: use db_schema or db_describe to explore the data source."
    )


# Elenco di nomi di funzione noti per leggere dati o file per nome (l'argomento
# è dentro una stringa, non un nome SQL: il controllo sui riferimenti non lo
# vede). Non è una garanzia, solo un elenco di nomi bloccati per somiglianza
# esatta sull'ultima parte del nome, qualificato o no, a prescindere dal
# perimetro: la garanzia vera resta nei grant dell'utenza sul database.
# L'elenco vale anche per un nome in posizione di relazione, senza parentesi:
# su PostgreSQL una funzione senza argomenti si chiama anche cosi', dentro un
# FROM o un JOIN, e leggerebbe le stesse cose.
_BLOCKED_FUNCTIONS = frozenset({
    # La famiglia *_to_xml/_to_xmlschema al completo: prende per nome una
    # query, una tabella, uno schema o l'intero database e ne restituisce dati
    # o struttura, anche solo come oracolo booleano dentro un WHERE.
    "query_to_xml",
    "query_to_xmlschema",
    "query_to_xml_and_xmlschema",
    "cursor_to_xml",
    "cursor_to_xmlschema",
    "table_to_xml",
    "table_to_xmlschema",
    "table_to_xml_and_xmlschema",
    "schema_to_xml",
    "schema_to_xmlschema",
    "schema_to_xml_and_xmlschema",
    "database_to_xml",
    "database_to_xmlschema",
    "database_to_xml_and_xmlschema",
    # I large object non stanno in nessuno schema: leggerli aggira il perimetro,
    # e lo_get ha una coppia (lo_open più loread) che legge gli stessi byte.
    "lo_get",
    "lo_open",
    "loread",
    "lowrite",
    "lo_lseek",
    "lo_lseek64",
    "lo_tell",
    "lo_tell64",
    "lo_close",
    "lo_unlink",
    "lo_truncate",
    "lo_put",
    "lo_from_bytea",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_ls_logdir",
    "pg_ls_waldir",
    "pg_stat_file",
    "lo_import",
    "lo_import_with_oid",
    "lo_export",
    "pg_stat_get_activity",
    "load_file",
    # La configurazione del server per intero, fuori da qualunque schema.
    "pg_show_all_settings",
    "pg_show_all_file_settings",
})

# Famiglie bloccate per prefisso, perché i nomi sono molti e ne arrivano di
# nuovi: pg_stat_get_backend_* (compresa pg_stat_get_backend_idset, che dà gli
# identificativi da passare alle altre) legge la sessione di un altro
# collegamento, query e letterali compresi, e morde quando più perimetri
# condividono la stessa utenza del database. pg_ls_* elenca il contenuto di una
# cartella del server: pg_ls_dir, pg_ls_logdir e pg_ls_waldir erano già
# bloccate per nome, le sorelle (pg_ls_tmpdir e le altre) leggono allo stesso
# modo fuori da qualunque schema. dblink* apre un collegamento verso qualunque
# database raggiungibile dal server e ne legge il risultato: la famiglia conta
# una dozzina di nomi (connect, connect_u, send_query, get_result, open, fetch,
# close e le altre) e bloccarne alcuni non serve, perché gli altri leggono gli
# stessi dati.
_BLOCKED_FUNCTION_PREFIXES = ("pg_stat_get_backend", "pg_ls_", "dblink")

# Funzioni che cambiano lo stato della sessione o del server invece di leggerlo.
# La connessione arriva da un pool: un search_path spostato con set_config e
# committato resterebbe sulla connessione e cambierebbe il perimetro delle
# letture successive (il pool la ripulisce al rientro, questo controllo la
# ferma prima). Le funzioni di sola lettura, per esempio current_setting o
# current_schema, restano ammesse.
_BLOCKED_SESSION_FUNCTIONS = frozenset({
    "set_config",
    "setseed",
    "pg_reload_conf",
    "pg_cancel_backend",
    "pg_terminate_backend",
    # I lock con nome di MySQL vivono nella sessione: presi sulla connessione
    # del pool restano lì e cambiano lo stato delle letture successive, e
    # interrogarli dice chi altro tiene un lock su quel server.
    "get_lock",
    "release_lock",
    "release_all_locks",
    "is_used_lock",
    "is_free_lock",
})
_BLOCKED_SESSION_FUNCTION_PREFIXES = (
    "pg_advisory",
    "pg_try_advisory",
    "pg_stat_reset",
)


def _function_refusal(name: str) -> Optional[QueryValidationError]:
    """L'eventuale rifiuto per il nome di una funzione chiamata nella query."""
    lowered = name.lower()
    if (
        lowered in _BLOCKED_SESSION_FUNCTIONS
        or lowered.startswith(_BLOCKED_SESSION_FUNCTION_PREFIXES)
    ):
        return _blocked_session_function_error(name)
    if lowered in _BLOCKED_FUNCTIONS or lowered.startswith(_BLOCKED_FUNCTION_PREFIXES):
        return _blocked_function_error(name)
    return None


def _blocked_session_function_error(name: str) -> QueryValidationError:
    return QueryValidationError(
        f"'{name}' changes the session or the server instead of reading it: the connection is "
        "shared, so the change would move the scope of the queries that come after it. "
        "Only read-only statements are allowed."
    )


def _unicode_identifier_error(reference: str) -> QueryValidationError:
    return QueryValidationError(
        f"{reference} is a U&\"...\" identifier with Unicode escapes: the name it stands for "
        "is not the one written, so it is not allowed anywhere in the query. "
        "Write the name of the table, the schema or the function as it is."
    )


def _unscoped_show_error(statement: str) -> QueryValidationError:
    return QueryValidationError(
        f"'{statement}' reads the server outside the scope (privileges, other sessions, "
        "server configuration), which is not available: use db_schema or db_describe to "
        "explore the data source."
    )


def _blocked_function_error(name: str) -> QueryValidationError:
    return QueryValidationError(
        f"'{name}' reads outside the scope by name (a table, a query or a file passed as a "
        "string argument): use db_schema or db_describe to explore the data source."
    )


def _check_show(tokens: list[_Token], allowed_schema: Optional[str]) -> None:
    """SHOW di MySQL: solo le forme di ``_ALLOWED_SHOW_FORMS`` e niente elenco
    dei database o degli schemi, su ogni perimetro, dedotto o confermato, come
    le regole sui cataloghi di sistema; il confronto del bersaglio di FROM/IN
    con lo schema consentito solo dove un perimetro c'è, perché senza perimetro
    non c'è niente con cui confrontarlo."""
    if not tokens or tokens[0].word() != "SHOW":
        return
    words = [t.word() for t in tokens]
    if len(words) > 1 and words[1] in ("DATABASES", "SCHEMAS"):
        # Con un perimetro l'elenco è un riferimento a quello che gli sta
        # fuori; senza perimetro non c'è uno schema da nominare nel rifiuto,
        # resta una lettura del server.
        if allowed_schema is None:
            raise _unscoped_show_error(f"SHOW {words[1]}")
        raise ScopeReferenceError(words[1].lower(), allowed_schema)
    if not any(tuple(words[1:1 + len(form)]) == form for form in _ALLOWED_SHOW_FORMS):
        raise _unscoped_show_error(" ".join(["SHOW", *(w for w in words[1:3] if w)]))
    if allowed_schema is None:
        return
    targets = [
        tokens[k + 1] for k in range(len(tokens) - 1)
        if words[k] in ("FROM", "IN") and tokens[k + 1].kind == "name"
    ]
    if _SHOW_TABLE_OBJECTS.intersection(words[1:3]):
        # SHOW COLUMNS FROM tabella [FROM database]: il primo nome è la tabella.
        targets = targets[1:]
    for target in targets:
        if not _same_name(target, allowed_schema):
            raise ScopeReferenceError(target.value, allowed_schema)


def _check_references(cleaned: str, allowed_schema: Optional[str]) -> None:
    """Solleva su un catalogo o un riferimento fuori perimetro, letto sotto
    entrambe le letture del backslash nelle stringhe a apici singoli: la
    query deve passare entrambe, non solo una a scelta."""
    for token_pattern in _TOKEN_PATTERNS:
        tokens = _tokenize(cleaned, token_pattern)
        for tok in tokens:
            # Un identificatore U&"..." nasconde il nome vero dietro gli escape
            # Unicode: nessuna lettura dentro il perimetro ne ha bisogno, e
            # confrontarlo per come è scritto leggerebbe un nome diverso da
            # quello che risolve il server. Si rifiuta e basta, su ogni
            # perimetro e su entrambi i percorsi, lettura e scrittura.
            if tok.kind == "uident":
                raise _unicode_identifier_error(tok.value)
        references, declared = _references(tokens)
        for ref in references:
            head = ref.parts[0]
            if ref.function or ref.relation:
                # Su PostgreSQL una funzione senza argomenti si chiama anche
                # senza parentesi, in posizione di relazione: l'elenco dei nomi
                # bloccati vale quindi sulla chiamata e sul nome di relazione
                # allo stesso modo, e il rifiuto è lo stesso nelle due forme.
                # Le colonne restano fuori: un nome che somiglia a una famiglia
                # bloccata, come dblinks_totali, non legge niente da sé.
                refusal = _function_refusal(ref.parts[-1].value)
                if refusal is not None:
                    raise refusal
            if head.value.lower() in _BLOCKED_CATALOGS:
                raise _catalog_error(ref.text)
            if len(ref.parts) == 1:
                if (
                    ref.relation and not ref.function
                    and head.value.lower().startswith(_CATALOG_RELATION_PREFIX)
                ):
                    raise _catalog_error(ref.text)
                continue
            if allowed_schema is None or _same_name(head, allowed_schema):
                continue
            if not ref.relation and not ref.function and head.value.lower() in declared:
                continue
            raise ScopeReferenceError(ref.text, allowed_schema)
        _check_show(tokens, allowed_schema)
