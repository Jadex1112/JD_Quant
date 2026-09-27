from datetime import UTC, datetime
from decimal import Decimal

import pytest

from jdquant.core.clock import SimulatedClock
from jdquant.core.events import EventBus
from jdquant.core.types import Side
from jdquant.marketdata.instruments import InstrumentRegistry
from jdquant.oms.orders import Fill, Liquidity
from jdquant.platform import demo_instruments
from jdquant.positions.engine import CostBasis, PositionEngine

BTC = "BINANCE:BTCUSDT"
_seq = iter(range(1, 10_000))


def _engine(basis=CostBasis.FIFO) -> PositionEngine:
    clock = SimulatedClock(datetime(2026, 1, 1, tzinfo=UTC))
    registry = InstrumentRegistry()
    for i in demo_instruments():
        registry.add(i)
    return PositionEngine(EventBus(clock), registry, basis)


def _fill(side: Side, qty: str, price: str, fee: str = "0") -> Fill:
    n = next(_seq)
    return Fill(
        f"F{n}",
        "O",
        "acct",
        "dep",
        BTC,
        side,
        Decimal(price),
        Decimal(qty),
        Decimal(fee),
        "USDT",
        Liquidity.TAKER,
        f"T{n}",
        datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_fifo_realized_pnl():
    """AC-28001."""
    engine = _engine()
    engine.apply_fill(_fill(Side.BUY, "1", "100"))
    engine.apply_fill(_fill(Side.BUY, "1", "110"))
    realized = engine.apply_fill(_fill(Side.SELL, "1", "120"))
    pos = engine.get_or_create("acct", BTC, "dep")
    assert realized == Decimal(20)
    assert pos.quantity == Decimal(1) and pos.average_entry_price == Decimal(110)


def test_average_cost_basis():
    """BR-28-05."""
    engine = _engine(CostBasis.AVERAGE)
    engine.apply_fill(_fill(Side.BUY, "1", "100"))
    engine.apply_fill(_fill(Side.BUY, "1", "110"))
    assert engine.apply_fill(_fill(Side.SELL, "1", "120")) == Decimal(15)
    assert engine.get_or_create("acct", BTC, "dep").average_entry_price == Decimal(105)


def test_position_flip_splits_the_fill():
    """AC-28002."""
    engine = _engine()
    engine.apply_fill(_fill(Side.BUY, "2", "100"))
    realized = engine.apply_fill(_fill(Side.SELL, "5", "90"))
    pos = engine.get_or_create("acct", BTC, "dep")
    assert realized == Decimal(-20)
    assert pos.quantity == Decimal(-3) and pos.average_entry_price == Decimal(90)
    assert pos.unrealized_pnl(Decimal(80)) == Decimal(30)


def test_short_position_realized_pnl_and_fees():
    engine = _engine()
    engine.apply_fill(_fill(Side.SELL, "1", "100", fee="0.1"))
    assert engine.apply_fill(_fill(Side.BUY, "1", "90", fee="0.09")) == Decimal(10)
    pos = engine.get_or_create("acct", BTC, "dep")
    assert pos.fees_paid == Decimal("0.19") and not pos.is_open


@pytest.mark.parametrize("basis", list(CostBasis))
def test_positions_sum_to_fills(basis):
    """INV-03: position equals the signed sum of fills."""
    engine = _engine(basis)
    fills = [
        _fill(Side.BUY, "3", "10"),
        _fill(Side.SELL, "1", "11"),
        _fill(Side.SELL, "4", "12"),
        _fill(Side.BUY, "2", "9"),
    ]
    for f in fills:
        engine.apply_fill(f)
    assert engine.net_quantity("acct", BTC) == sum(f.quantity * f.side.sign for f in fills)
