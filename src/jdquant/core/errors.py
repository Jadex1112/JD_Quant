"""Structured platform errors (SRS Part C, C.8)."""

from __future__ import annotations

from typing import Any


class PlatformError(Exception):
    """Error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, field: str | None = None, details: Any = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.field = field
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            data["field"] = self.field
        if self.details is not None:
            data["details"] = self.details
        return data


class NotFoundError(PlatformError):
    pass


class ValidationError(PlatformError):
    def __init__(self, code: str, violations: list[dict[str, str]]):
        message = "; ".join(f"{v['field']}: {v['message']}" for v in violations)
        super().__init__(code, message, details=violations)
        self.violations = violations
