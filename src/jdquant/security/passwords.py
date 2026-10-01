"""Password hashing with a memory-hard KDF and password policy (FR-40020, FR-40021)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from jdquant.core.errors import ValidationError

_N, _R, _P, _DKLEN = 2**14, 8, 1, 32
MIN_LENGTH = 12
_COMMON = frozenset(
    {
        "password1234",
        "123456789012",
        "qwertyuiopas",
        "passwordpassword",
        "letmein12345",
        "iloveyou1234",
        "adminadmin12",
        "welcome12345",
        "changeme1234",
        "trustno1trustno1",
    }
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    expected = base64.b64decode(digest)
    actual = hashlib.scrypt(
        password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
    )
    return hmac.compare_digest(actual, expected)


def check_policy(password: str, email: str = "") -> None:
    violations = []
    if len(password) < MIN_LENGTH:
        violations.append({"field": "password", "message": f"must be at least {MIN_LENGTH} characters"})
    if password.lower() in _COMMON:
        violations.append({"field": "password", "message": "is a commonly used password"})
    if email and email.split("@")[0].lower() in password.lower():
        violations.append({"field": "password", "message": "must not contain the email name"})
    if violations:
        raise ValidationError("PASSWORD_POLICY", violations)


# A fixed hash used to equalize timing when the user does not exist (FR-40026).
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
