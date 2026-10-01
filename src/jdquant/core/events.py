"""In-process Central Messaging Engine (Chapter 82; API-82007 embedded bus for T1)."""

from __future__ import annotations

import fnmatch
import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jdquant.core.clock import Clock

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    """Event envelope (82.2)."""

    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime
    producer: str
    partition_key: str = ""
    sequence: int = 0
    correlation_id: str | None = None
    causation_id: str | None = None
    schema_version: int = 1
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))


Handler = Callable[[Event], None]


@dataclass
class DeadLetter:
    event: Event
    handler: str
    error: str


class EventBus:
    """Synchronous bus, ordered per partition.

    Critical subscribers (persistence, audit) run first and their failures abort the publishing
    operation; failures of ordinary subscribers are isolated into a dead-letter list.
    """

    def __init__(self, clock: Clock):
        self._clock = clock
        self._critical: list[tuple[str, Handler]] = []
        self._subscriptions: list[tuple[str, Handler]] = []
        self._sequences: dict[str, int] = defaultdict(int)
        self.dead_letters: list[DeadLetter] = []

    def subscribe(self, pattern: str, handler: Handler, *, critical: bool = False) -> None:
        (self._critical if critical else self._subscriptions).append((pattern, handler))

    def unsubscribe(self, handler: Handler) -> None:
        self._critical = [(p, h) for p, h in self._critical if h != handler]
        self._subscriptions = [(p, h) for p, h in self._subscriptions if h != handler]

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        producer: str,
        partition_key: str = "",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> Event:
        self._sequences[partition_key] += 1
        event = Event(
            event_type=event_type,
            payload=payload,
            occurred_at=self._clock.now(),
            producer=producer,
            partition_key=partition_key,
            sequence=self._sequences[partition_key],
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        for pattern, handler in list(self._critical):
            if fnmatch.fnmatchcase(event_type, pattern):
                handler(event)
        for pattern, handler in list(self._subscriptions):
            if fnmatch.fnmatchcase(event_type, pattern):
                try:
                    handler(event)
                except Exception as exc:  # isolate consumers from each other (API-82006)
                    log.exception("event handler failed for %s", event_type)
                    self.dead_letters.append(
                        DeadLetter(event, getattr(handler, "__qualname__", "?"), repr(exc))
                    )
        return event
