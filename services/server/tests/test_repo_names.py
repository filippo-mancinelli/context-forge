import pytest

from src.catalog.names import normalize_repo_url, repo_name_for, repo_url_name


@pytest.mark.parametrize("raw", [
    "https://git.example.org/aster/aster-desk",
    "http://git.example.org/aster/aster-desk",
    "https://git.example.org/aster/aster-desk.git",
    "https://git.example.org/aster/aster-desk/",
    "https://GIT.Example.ORG/aster/aster-desk.git/",
    "https://oauth2:secret@git.example.org/aster/aster-desk.git",
    "  https://git.example.org/aster/aster-desk  ",
])
def test_equivalent_urls_normalize_to_the_same_value(raw):
    assert normalize_repo_url(raw) == "https://git.example.org/aster/aster-desk"


def test_path_case_and_port_are_kept():
    assert normalize_repo_url("http://Git.Example.com:8443/Team/AsterSign.git") == "https://git.example.com:8443/Team/AsterSign"


@pytest.mark.parametrize("raw", ["file:///tmp/remote.git", "git@github.com:acme/widget.git"])
def test_non_http_addresses_are_left_alone(raw):
    assert normalize_repo_url(raw) == raw


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_url_is_none(raw):
    assert normalize_repo_url(raw) is None


def test_name_is_the_last_url_segment():
    assert repo_url_name("https://git.example.org/aster/AsterSign.git") == "AsterSign"


def test_a_taken_name_gets_the_branch_regardless_of_case():
    assert repo_name_for("https://git.example.org/aster/AsterSign", "develop", {"astersign"}) == "AsterSign@develop"


def test_a_free_name_is_used_as_is():
    assert repo_name_for("https://git.example.org/aster/aster-desk", "main", {"astersign"}) == "aster-desk"


def test_a_name_whose_branch_variant_is_also_taken_gets_a_number():
    assert repo_name_for("https://h.example/x/app", "main", {"app", "app@main"}) == "app@main-2"
