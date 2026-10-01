"""RFC 6238 time-based one-time passwords (FR-40022)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _counter_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def code_at(secret: str, at: datetime) -> str:
    return _counter_code(secret, int(at.timestamp()) // STEP)


def verify(secret: str, code: str, at: datetime, window: int = 1) -> bool:
    return matched_counter(secret, code, at, window) is not None


def matched_counter(secret: str, code: str, at: datetime, window: int = 1) -> int | None:
    """Time step the code belongs to, so callers can reject replays of an already used step."""
    counter = int(at.timestamp()) // STEP
    code = code.strip().replace(" ", "")
    for delta in range(-window, window + 1):
        if hmac.compare_digest(_counter_code(secret, counter + delta), code):
            return counter + delta
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "JD Quant AI") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}"
