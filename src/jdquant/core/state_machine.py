"""Declarative state machines (FR-89001)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Generic, TypeVar

from jdquant.core.errors import PlatformError

S = TypeVar("S", bound=Enum)


class StateMachine(Generic[S]):
    def __init__(self, name: str, transitions: Mapping[S, Iterable[S]]):
        self.name = name
        self._transitions: dict[S, frozenset[S]] = {k: frozenset(v) for k, v in transitions.items()}

    def can_transition(self, current: S, target: S) -> bool:
        return target in self._transitions.get(current, frozenset())

    def assert_transition(self, current: S, target: S) -> None:
        if not self.can_transition(current, target):
            raise PlatformError(
                "INVALID_STATE_TRANSITION",
                f"{self.name}: transition {current.value} -> {target.value} is not permitted",
            )
