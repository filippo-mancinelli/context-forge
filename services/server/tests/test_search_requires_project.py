import asyncio
import inspect

import pytest

from src import search


@pytest.mark.parametrize("fn_name", [
    "search_repo_chunks",
    "search_repo_symbols",
    "search_web_chunks",
    "search_kb_chunks",
])
def test_scoped_search_rejects_null_project(fn_name):
    fn = getattr(search, fn_name)
    params = inspect.signature(fn).parameters
    # Costruisce kwargs minimi: tutti i parametri senza default valorizzati a
    # placeholder, project_id forzato a None. Il guard deve scattare prima di
    # qualunque accesso al pool.
    kwargs = {}
    for name, p in params.items():
        if name == "project_id":
            kwargs[name] = None
        elif p.default is inspect.Parameter.empty:
            kwargs[name] = "x" if p.annotation is str else 1
    with pytest.raises(ValueError, match="project_id is required"):
        asyncio.run(fn(**kwargs))
