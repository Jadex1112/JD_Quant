"""Strategy templates and backtests (Chapters 24–25)."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Request

from jdquant.api.deps import ctx, require
from jdquant.api.schemas import BacktestIn, BacktestOut, StrategyTemplateOut, TradeOut
from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.execution.simulator import FeeSchedule
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.risk.engine import RiskLimit
from jdquant.security.identity import Principal
from jdquant.strategy.templates import TEMPLATES

router = APIRouter(prefix="/api/v1", tags=["research"])


@router.get("/strategy-templates", response_model=list[StrategyTemplateOut])
def strategy_templates(principal: Principal = Depends(require("strategy:view"))) -> list[StrategyTemplateOut]:
    return [
        StrategyTemplateOut(
            name=cls.name,
            version=cls.version,
            description=cls.description,
            parameters={
                name: {
                    "type": prm.type.__name__,
                    "default": str(prm.default) if isinstance(prm.default, Decimal) else prm.default,
                    "min": None if prm.min is None else str(prm.min),
                    "max": None if prm.max is None else str(prm.max),
                    "description": prm.description,
                }
                for name, prm in cls.parameters.items()
            },
        )
        for cls in TEMPLATES.values()
    ]


@router.post("/backtests", response_model=BacktestOut, status_code=201)
def create_backtest(
    body: BacktestIn, request: Request, principal: Principal = Depends(require("backtest:run"))
):
    c = ctx(request)
    instrument = c.platform.instruments.get(body.instrument_id)
    d = body.data
    candles = random_walk_candles(
        instrument,
        d.start,
        d.bars,
        interval_seconds=d.interval_seconds,
        start_price=d.start_price,
        volatility=d.volatility,
        drift=d.drift,
        seed=d.seed,
    )
    result = run_backtest(
        BacktestConfig(
            strategy=body.strategy,
            instruments=[instrument],
            candles={instrument.instrument_id: candles},
            parameters=body.parameters,
            initial_capital=body.initial_capital,
            fees=FeeSchedule(body.maker_fee_bps, body.taker_fee_bps),
            slippage_bps=body.slippage_bps,
            risk_limits=[RiskLimit(r.limit_type, r.threshold) for r in body.risk_limits],
            seed=d.seed,
        )
    )
    return BacktestOut(
        reproducibility_hash=result.reproducibility_hash,
        final_equity=result.final_equity,
        metrics=result.metrics,
        order_count=len(result.orders),
        fill_count=len(result.fills),
        trades=[TradeOut(**{**t.__dict__, "net_pnl": t.net_pnl}) for t in result.trades],
        equity_curve=result.equity_curve,
        assumptions=list(result.assumptions),
    )
