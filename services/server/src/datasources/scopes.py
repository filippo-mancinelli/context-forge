"""Forma del perimetro di un datasource, motore per motore.

La parola "schema" non significa la stessa cosa su tutti i motori: su
PostgreSQL una connessione ha un database e più schemi, su MySQL e MariaDB
schema e database coincidono, SQLite non ha né l'uno né l'altro. Qui vive
l'unica interpretazione di quella differenza: quali parti compongono il
perimetro, come si normalizzano, quale schema si introspeziona, quale database
entra nella URL e quali opzioni restringono la connessione.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Optional

from .engines import SUPPORTED_ENGINES

# Parti che compongono il perimetro per ciascun motore supportato.
_SHAPES: dict[str, tuple[str, ...]] = {
    "postgresql": ("database", "schema"),
    "mysql": ("database",),
    "mariadb": ("database",),
    "sqlite": (),
}

_DEFAULT_SCHEMA = "public"


class ScopeShapeError(ValueError):
    """Il perimetro non ha la forma attesa dal motore."""


def scope_shape(engine: str) -> tuple[str, ...]:
    if engine not in SUPPORTED_ENGINES:
        raise ScopeShapeError(f"Unsupported engine '{engine}'")
    return _SHAPES[engine]


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


# Caratteri che un nome di database o di schema del perimetro non può
# contenere: finirebbero nelle opzioni di sessione di libpq, che separano i
# flag sugli spazi e interpretano barre rovesciate e virgolette.
_UNSAFE_CHARS = frozenset("\\'\",")


def _check_identifier(kind: str, value: Optional[str]) -> None:
    if value is None:
        return
    for ch in value:
        if ch in _UNSAFE_CHARS or ch.isspace() or unicodedata.category(ch) == "Cc":
            raise ScopeShapeError(
                f"The scope {kind} {value!r} contains characters that are not allowed"
            )


def normalize_scope(
    engine: str, database: Optional[str], schema: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """Perimetro in forma canonica, o ``ScopeShapeError`` se non regge il motore."""
    shape = scope_shape(engine)
    database = _clean(database)
    schema = _clean(schema)
    _check_identifier("database", database)
    _check_identifier("schema", schema)

    if not shape:
        if database or schema:
            raise ScopeShapeError(f"Engine '{engine}' has no database or schema to choose")
        return None, None

    if database is None:
        raise ScopeShapeError(f"Engine '{engine}' needs a database in the scope")

    if "schema" not in shape:
        # Schema e database coincidono: un valore diverso sarebbe un altro perimetro.
        if schema is not None and schema != database:
            raise ScopeShapeError(
                f"Engine '{engine}' has no schema separate from the database '{database}'"
            )
        return database, None

    return database, schema or _DEFAULT_SCHEMA


def default_scope(connection: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Perimetro che una connessione serve oggi, senza scelta del progetto."""
    engine = connection["engine"]
    if not scope_shape(engine):
        return None, None
    return normalize_scope(engine, connection.get("database_name"), None)


def introspection_schema(
    engine: str, database: Optional[str], schema: Optional[str]
) -> Optional[str]:
    """Schema da passare all'introspezione per questo perimetro."""
    shape = scope_shape(engine)
    if not shape:
        return None
    if "schema" in shape:
        return schema
    return database


def url_database(
    engine: str, connection_database: Optional[str], scope_database: Optional[str]
) -> Optional[str]:
    """Database che entra nella URL: quello del perimetro, o il predefinito."""
    if not scope_shape(engine):
        return connection_database
    return _clean(scope_database) or connection_database


def connect_options(engine: str, schema: Optional[str]) -> dict[str, str]:
    """Opzioni che restringono la connessione al perimetro. Lo schema va tra
    virgolette doppie, così PostgreSQL lo prende col maiuscolo del nome reale."""
    if engine == "postgresql" and _clean(schema):
        return {"options": f'-csearch_path="{schema}"'}
    return {}


def scope_label(engine: str, database: Optional[str], schema: Optional[str]) -> str:
    """Perimetro in forma leggibile, usata negli alias e nei messaggi."""
    shape = scope_shape(engine)
    if not shape:
        return "(file)"
    if not _clean(database):
        # Nessun database scelto né predefinito: la connessione lavora su quello
        # che il server assegna all'utenza.
        return "(default database)"
    if "schema" in shape and _clean(schema):
        return f"{database}.{schema}"
    return str(database)


class ScopeViolationError(ValueError):
    """Una richiesta esce dal perimetro confermato del collegamento.

    Porta l'alias del collegamento, l'etichetta del suo perimetro e gli alias
    disponibili nel progetto, così chi la riceve può scegliere il collegamento
    giusto o aggiungere il perimetro che manca.
    """

    def __init__(self, message: str, alias: str, scope: str, aliases: list[str]):
        super().__init__(message)
        self.alias = alias
        self.scope = scope
        self.aliases = aliases
