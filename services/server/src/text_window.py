"""Finestra di righe su un testo, con i limiti normalizzati.

Restituire un file intero quando serve una funzione costa al chiamante decine
di migliaia di token per una domanda che ne meritava trecento. Qui si taglia la
finestra richiesta, riportando sempre quali righe si stanno guardando e se il
resto del file esiste ancora: senza quei due dati chi legge non sa se ha visto
tutto.
"""
from __future__ import annotations

from typing import Optional


def slice_lines(
    content: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> dict:
    """Ritaglia le righe da ``start_line`` a ``end_line`` incluse (1-based).

    Senza limiti restituisce tutto. Un limite oltre la fine del file viene
    accorciato invece di dare errore: chiedere venti righe attorno a una che sta
    in fondo è normale. Limiti incoerenti — non interi, sotto 1, o fine prima
    dell'inizio — sono un errore del chiamante e vengono segnalati.
    """
    lines = content.splitlines()
    total = len(lines)

    if start_line is None and end_line is None:
        return {
            "content": content,
            "start_line": 1 if total else 0,
            "end_line": total,
            "total_lines": total,
            "truncated": False,
        }

    for label, value in (("start_line", start_line), ("end_line", end_line)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"{label} must be an integer")
        if value is not None and value < 1:
            raise ValueError(f"{label} must be 1 or greater")
    if start_line is not None and end_line is not None and end_line < start_line:
        raise ValueError("end_line must not be smaller than start_line")

    start = start_line or 1
    end = end_line if end_line is not None else total
    if start > total:
        return {
            "content": "",
            "start_line": start,
            "end_line": start - 1,
            "total_lines": total,
            "truncated": total > 0,
        }
    end = min(end, total)
    window = lines[start - 1 : end]
    return {
        "content": "\n".join(window),
        "start_line": start,
        "end_line": end,
        "total_lines": total,
        "truncated": start > 1 or end < total,
    }
