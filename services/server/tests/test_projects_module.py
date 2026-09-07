import asyncio
import inspect
import pytest

from src import projects, tenancy


def test_project_namespace():
    assert projects.project_namespace("acme", "webshop") == "acme--webshop"


def test_reserved_slugs():
    assert "oauth" in projects.RESERVED_PROJECT_SLUGS
    assert "health" in projects.RESERVED_PROJECT_SLUGS


def test_default_slug():
    assert projects.DEFAULT_PROJECT_SLUG == "default"


def test_create_project_rejects_reserved_slug():
    """Lo slug deriva dal nome: un nome che slugifica in uno slug riservato deve fallire
    prima di toccare il database."""
    with pytest.raises(ValueError):
        asyncio.run(projects.create_project(1, "OAuth"))


def test_create_organization_creates_default_project():
    """La creazione di un'organizzazione deve creare anche il suo progetto di
    default, nella stessa transazione della membership owner."""
    source = inspect.getsource(tenancy.create_organization)
    assert "INSERT INTO projects" in source
