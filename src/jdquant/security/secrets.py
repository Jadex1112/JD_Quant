"""Encrypted secret store (CON-080, SEC-70003): values are encrypted at rest and never returned by APIs."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from jdquant.core.errors import NotFoundError, PlatformError
from jdquant.persistence.store import Store


def load_master_key(data_dir: Path | None) -> bytes:
    """Key from JDQ_MASTER_KEY, else a key file created with owner-only permissions."""
    env = os.environ.get("JDQ_MASTER_KEY")
    if env:
        return env.encode()
    if data_dir is None:
        return Fernet.generate_key()
    path = data_dir / "master.key"
    if not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(Fernet.generate_key())
    return path.read_bytes().strip()


class SecretBox:
    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode()
        except InvalidToken:
            raise PlatformError(
                "SECRET_UNREADABLE", "secret cannot be decrypted with the current key"
            ) from None


class SecretStore:
    def __init__(self, store: Store, box: SecretBox):
        self._store = store
        self._box = box

    def put(self, name: str, value: str) -> None:
        self._store.execute(
            "INSERT INTO secrets(name, ciphertext) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET "
            "ciphertext = excluded.ciphertext, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
            (name, self._box.encrypt(value)),
        )

    def get(self, name: str) -> str:
        rows = self._store.query("SELECT ciphertext FROM secrets WHERE name = ?", (name,))
        if not rows:
            raise NotFoundError("SECRET_NOT_FOUND", f"secret {name} is not configured")
        return self._box.decrypt(rows[0]["ciphertext"])

    def exists(self, name: str) -> bool:
        return bool(self._store.query("SELECT 1 FROM secrets WHERE name = ?", (name,)))

    def delete(self, name: str) -> None:
        self._store.execute("DELETE FROM secrets WHERE name = ?", (name,))
