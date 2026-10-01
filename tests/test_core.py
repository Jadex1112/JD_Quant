from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import PlatformError
from jdquant.core.events import EventBus
from jdquant.core.types import to_decimal
from jdquant.oms.orders import ORDER_STATE_MACHINE, OrderStatus


def test_binary_floats_are_rejected_for_money():
    """CON-022."""
    assert to_decimal("0.1") == Decimal("0.1")
    with pytest.raises(TypeError):
        to_decimal(0.1)


def test_simulated_clock_never_moves_backwards():
    clock = SimulatedClock(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        clock.set(datetime(2025, 12, 31, tzinfo=UTC))


def test_clock_requires_utc():
    """CON-023."""
    with pytest.raises(ValueError):
        SimulatedClock(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        SimulatedClock(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=5, minutes=30))))


def test_undefined_transitions_are_rejected():
    """FR-89001."""
    with pytest.raises(PlatformError) as err:
        ORDER_STATE_MACHINE.assert_transition(OrderStatus.FILLED, OrderStatus.OPEN)
    assert err.value.code == "INVALID_STATE_TRANSITION"


def test_event_bus_isolates_failing_handlers_and_sequences_partitions():
    bus = EventBus(SimulatedClock(datetime(2026, 1, 1, tzinfo=UTC)))
    received = []

    def broken(event):
        raise RuntimeError("boom")

    bus.subscribe("order.*", broken)
    bus.subscribe("order.*", received.append)
    first = bus.publish("order.created", {}, producer="test", partition_key="a")
    second = bus.publish("order.created", {}, producer="test", partition_key="a")
    assert [e.sequence for e in received] == [1, 2]
    assert (first.sequence, second.sequence) == (1, 2)
    assert len(bus.dead_letters) == 2
