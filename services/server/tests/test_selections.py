import pytest

from src.catalog import selections


def test_unknown_kind_is_rejected():
    with pytest.raises(selections.UnknownKindError, match="repos"):
        selections.kind_spec("repos")


def test_known_kinds_map_to_their_tables():
    assert selections.kind_spec("folders")["link"] == "project_ssh_sources"
    assert selections.kind_spec("databases")["column"] == "db_connection_id"
