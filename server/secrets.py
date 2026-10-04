"""Credential encryption: Fernet with the master key from OMNICLOUD_MASTER_KEY.

Tokens are submitted once (account creation), stored as ciphertext + last4,
never returned by any API, never logged. No update-secret path: delete the
account and re-add it (read-only-by-default credential storage).
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from . import config


class SecretsUnavailable(Exception):
    """Raised when OMNICLOUD_MASTER_KEY is not set but a credential operation
    was requested. The panel still runs read-only from cache."""


def _fernet() -> Fernet | None:
    if not config.MASTER_KEY:
        return None
    return Fernet(config.MASTER_KEY.encode())


def encrypt(token: str) -> bytes:
    f = _fernet()
    if f is None:
        raise SecretsUnavailable(
            "OMNICLOUD_MASTER_KEY is not set - generate one with "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return f.encrypt(token.encode())


def decrypt(ciphertext: bytes) -> str:
    f = _fernet()
    if f is None:
        raise SecretsUnavailable("OMNICLOUD_MASTER_KEY is not set")
    try:
        return f.decrypt(ciphertext).decode()
    except InvalidToken:
        raise SecretsUnavailable("credential ciphertext does not match the current master key")

