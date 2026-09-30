import pytest

from src.catalog import selections


def test_unknown_kind_is_rejected():
    with pytest.raises(selections.UnknownKindError, match="connectors"):
        selections.kind_spec("connectors")


def test_known_kinds_map_to_their_tables():
    assert selections.kind_spec("folders")["link"] == "project_ssh_sources"
    assert selections.kind_spec("databases")["column"] == "db_connection_id"
    assert selections.kind_spec("repos") == {"table": "repos", "link": "project_repos", "column": "repo_id"}
    # Per un database il collegamento è il perimetro.
    assert selections.kind_spec("databases")["link"] == "project_db_scopes"
