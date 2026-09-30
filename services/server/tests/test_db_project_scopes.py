from src.datasources.project_scopes import default_alias


def test_first_link_takes_the_connection_name():
    assert default_alias([], "erp", "app.public", True) == "erp"


def test_later_links_add_the_scope_label():
    assert default_alias(["erp"], "erp", "app.vendite", False) == "erp/app.vendite"


def test_a_taken_alias_gets_a_suffix_ignoring_case():
    assert default_alias(["ERP"], "erp", "app.public", True) == "erp-2"
    assert default_alias(["erp", "erp/app.vendite"], "erp", "app.vendite", False) == "erp/app.vendite-2"


def test_a_numeric_connection_name_does_not_yield_a_numeric_alias():
    # Un alias fatto di sole cifre sembrerebbe l'id di una connessione.
    assert not default_alias([], "42", "app.public", True).isdigit()
