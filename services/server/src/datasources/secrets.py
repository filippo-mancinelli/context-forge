"""Encryption at rest for data-source credentials.

When ``ENCRYPTION_KEY`` is set, passwords are encrypted with Fernet using a key
derived from it (SHA-256). Without it, values are stored base64-obfuscated with
a distinct prefix so deployments can enable real encryption later without a
schema change. Stored values are self-describing via their prefix.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

_ENC_PREFIX = "enc:"
_PLAIN_PREFIX = "b64:"


class SecretError(Exception):
    pass


def _fernet() -> Optional[Fernet]:
    from ..config import get_settings

    key = get_settings().encryption_key
    if not key:
        return None
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    f = _fernet()
    if f is None:
        return _PLAIN_PREFIX + base64.b64encode(value.encode("utf-8")).decode("ascii")
    return _ENC_PREFIX + f.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith(_PLAIN_PREFIX):
        return base64.b64decode(stored[len(_PLAIN_PREFIX):]).decode("utf-8")
    if stored.startswith(_ENC_PREFIX):
        f = _fernet()
        if f is None:
            raise SecretError(
                "Credential was encrypted but ENCRYPTION_KEY is not configured"
            )
        try:
            return f.decrypt(stored[len(_ENC_PREFIX):].encode("ascii")).decode("utf-8")
        except InvalidToken as e:
            raise SecretError("Credential cannot be decrypted (wrong ENCRYPTION_KEY?)") from e
    # Legacy/unknown format: treat as plaintext.
    return stored
