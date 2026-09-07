from src.ssh_sources import service


def _row(**over):
    base = {
        "id": 1, "org_id": 1, "project_id": 2, "name": "prod", "host": "h",
        "port": 22, "username": "u", "auth_method": "key",
        "password_enc": "", "private_key_enc": "enc:blob", "root_path": "/etc/askme",
        "include_globs": "*.conf", "exclude_globs": "secrets*", "description": None,
        "status": "unknown", "error_message": None, "last_checked_at": None,
        "created_at": None, "updated_at": None,
    }
    base.update(over)
    return base


def test_record_hides_secrets_and_flags_presence():
    out = service._record_to_dict(_row())
    assert out["has_secret"] is True
    assert "private_key_enc" not in out
    assert "password_enc" not in out
    assert out["root_path"] == "/etc/askme"


def test_record_include_secret_keeps_encrypted_blobs():
    out = service._record_to_dict(_row(), include_secret=True)
    assert out["private_key_enc"] == "enc:blob"


def test_decrypted_conn_shape(monkeypatch):
    monkeypatch.setattr(service, "decrypt_secret", lambda s: f"clear:{s}" if s else "")
    # decrypted_conn opera sul record grezzo (con *_enc)
    conn = service.decrypted_conn(_row())
    assert conn["host"] == "h"
    assert conn["auth_method"] == "key"
    assert conn["root_path"] == "/etc/askme"
    assert conn["include_globs"] == "*.conf"
    assert conn["private_key"] == "clear:enc:blob"
