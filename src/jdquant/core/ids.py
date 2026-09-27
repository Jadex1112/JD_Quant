"""Identifier generation: random for live use, sequential for deterministic simulation (QAS-09)."""

from __future__ import annotations

import itertools
import uuid
from typing import Protocol


class IdGenerator(Protocol):
    def next(self, prefix: str) -> str: ...


class UuidIds:
    def next(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


class SequentialIds:
    def __init__(self) -> None:
        self._counters: dict[str, itertools.count[int]] = {}

    def next(self, prefix: str) -> str:
        counter = self._counters.setdefault(prefix, itertools.count(1))
        return f"{prefix}-{next(counter):08d}"
