"""Common data types (SRS Part C, C.7)."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

ZERO = Decimal(0)


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Convert to Decimal, refusing binary floats for monetary values (CON-022)."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("binary floating-point values are not permitted for monetary quantities")
    if isinstance(value, Decimal):
        return value
    return Decimal(value)
