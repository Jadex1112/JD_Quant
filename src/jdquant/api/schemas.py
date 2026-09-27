"""REST request/response models. Decimals travel as strings (Chapter 80.2, CON-022)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer

from jdquant.core.types import Side
from jdquant.oms.orders import OrderType, TimeInForce
from jdquant.risk.engine import LimitType
from jdquant.trading.engine import KillSwitchAction, KillSwitchScope


def _no_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("decimal values must be sent as strings, not JSON numbers with fractions")
    return value


Dec = Annotated[Decimal, BeforeValidator(_no_float), PlainSerializer(lambda d: str(d), return_type=str)]


class OrderIn(BaseModel):
    account_id: str
    instrument_id: str
    side: Side
    order_type: OrderType
    quantity: Dec
    limit_price: Dec | None = None
    stop_price: Dec | None = None
    time_in_force: TimeInForce | None = None
    post_only: bool = False
    reduce_only: bool = False
    tags: dict[str, str] = Field(default_factory=dict)


class OrderOut(BaseModel):
    order_id: str
    client_order_id: str
    account_id: str
    deployment_id: str
    instrument_id: str
    side: Side
    order_type: OrderType
    time_in_force: TimeInForce
    quantity: Dec
    limit_price: Dec | None
    status: str
    filled_quantity: Dec
    remaining_quantity: Dec
    average_fill_price: Dec | None
    fees: dict[str, Dec]
    reject_code: str | None
    reject_reason: str | None
    risk_decision_id: str | None
    source: str
    created_at: datetime


class PositionOut(BaseModel):
    account_id: str
    instrument_id: str
    deployment_id: str
    quantity: Dec
    average_entry_price: Dec
    realized_pnl: Dec
    unrealized_pnl: Dec | None
    fees_paid: Dec


class QuoteIn(BaseModel):
    instrument_id: str
    bid_price: Dec
    bid_size: Dec
    ask_price: Dec
    ask_size: Dec


class InstrumentOut(BaseModel):
    instrument_id: str
    venue: str
    symbol: str
    asset_class: str
    base_asset: str
    quote_asset: str
    tick_size: Dec
    lot_size: Dec
    min_quantity: Dec
    min_notional: Dec | None
    status: str
    reference_price: Dec | None
    feed_status: str


class KillSwitchIn(BaseModel):
    scope: KillSwitchScope
    action: KillSwitchAction
    reason: str = Field(min_length=1)
    target_id: str | None = None


class KillSwitchReleaseIn(BaseModel):
    reason: str = Field(min_length=1)


class KillSwitchOut(BaseModel):
    kill_switch_id: str
    scope: KillSwitchScope
    target_id: str | None
    action: KillSwitchAction
    reason: str
    triggered_by: str
    triggered_at: datetime
    active: bool
    released_by: str | None
    released_at: datetime | None


class RiskLimitIn(BaseModel):
    limit_type: LimitType
    threshold: Dec


class SyntheticDataIn(BaseModel):
    start: datetime
    bars: int = Field(ge=50, le=100_000)
    interval_seconds: int = Field(default=3600, ge=1)
    start_price: Dec = Decimal(100)
    volatility: float = Field(default=0.01, gt=0, le=0.5)
    drift: float = 0.0
    seed: int = 7


class BacktestIn(BaseModel):
    strategy: str
    instrument_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    initial_capital: Dec = Decimal(100_000)
    maker_fee_bps: Dec = Decimal(10)
    taker_fee_bps: Dec = Decimal(10)
    slippage_bps: Dec = Decimal(1)
    risk_limits: list[RiskLimitIn] = Field(default_factory=list)
    data: SyntheticDataIn


class TradeOut(BaseModel):
    instrument_id: str
    direction: Side
    quantity: Dec
    entry_time: datetime
    exit_time: datetime
    entry_price: Dec
    exit_price: Dec
    gross_pnl: Dec
    fees: Dec
    net_pnl: Dec


class BacktestOut(BaseModel):
    reproducibility_hash: str
    final_equity: Dec
    metrics: dict[str, float | int | None]
    order_count: int
    fill_count: int
    trades: list[TradeOut]
    equity_curve: list[tuple[datetime, Dec]]
    assumptions: list[str]


class StrategyTemplateOut(BaseModel):
    name: str
    version: str
    description: str
    parameters: dict[str, dict[str, Any]]
