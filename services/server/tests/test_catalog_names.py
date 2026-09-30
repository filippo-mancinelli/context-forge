from datetime import datetime, timezone

from src.catalog.migration import choose_secret
from src.catalog.names import machine_name, normalize_root, unique_name


def _candidate(key, status="unknown", checked=None):
    return {"auth_method": "key", "password": "", "private_key": key,
            "status": status, "last_checked_at": checked}


def test_machine_name_shows_the_port_only_when_it_is_not_22():
    assert machine_name("astercare", "192.168.0.206") == "astercare@192.168.0.206"
    assert machine_name("cinello", "195.223.248.54", 15250) == "cinello@195.223.248.54:15250"


def test_unique_name_adds_the_first_free_suffix_ignoring_case():
    assert unique_name("logs", set()) == "logs"
    assert unique_name("Logs", {"logs"}) == "Logs-2"
    assert unique_name("logs", {"logs", "logs-2"}) == "logs-3"


def test_normalize_root_removes_trailing_and_double_slashes():
    assert normalize_root(" /srv//astercare/logs/ ") == "/srv/astercare/logs"


def test_choose_secret_prefers_the_most_used_one():
    candidates = [_candidate("K1"), _candidate("K1"),
                  _candidate("K2", "ok", datetime(2026, 8, 6, tzinfo=timezone.utc))]
    assert choose_secret(candidates)["private_key"] == "K1"


def test_choose_secret_tie_goes_to_the_latest_successful_check():
    candidates = [_candidate("K1", "ok", datetime(2026, 7, 31, tzinfo=timezone.utc)),
                  _candidate("K2", "ok", datetime(2026, 8, 6, tzinfo=timezone.utc))]
    assert choose_secret(candidates)["private_key"] == "K2"


def test_choose_secret_tie_without_checks_keeps_the_first():
    assert choose_secret([_candidate("K1"), _candidate("K2")])["private_key"] == "K1"
