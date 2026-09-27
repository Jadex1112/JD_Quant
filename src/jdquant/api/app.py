"""Public REST API v1 (Chapter 80) for the single-node paper-trading platform."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from jdquant import __version__
from jdquant.api.schemas import (
    BacktestIn,
    BacktestOut,
    InstrumentOut,
    KillSwitchIn,
    KillSwitchOut,
    KillSwitchReleaseIn,
    OrderIn,
    OrderOut,
    PositionOut,
    QuoteIn,
    StrategyTemplateOut,
    TradeOut,
)
from jdquant.backtest.engine import BacktestConfig, run_backtest
from jdquant.core.errors import NotFoundError, PlatformError, ValidationError
from jdquant.execution.simulator import FeeSchedule
from jdquant.marketdata.records import Quote
from jdquant.marketdata.synthetic import random_walk_candles
from jdquant.oms.orders import Order, OrderRequest, OrderSource, OrderStatus
from jdquant.platform import Platform, build_paper_platform
from jdquant.risk.engine import RiskLimit
from jdquant.strategy.templates import TEMPLATES
from jdquant.trading.engine import KillSwitch

API_USER = "local-user"  # authentication is not yet implemented (Chapters 39-40)

_STATUS_BY_CODE = {
    "INVALID_STATE_TRANSITION": 409,
    "ORDER_NOT_MODIFIABLE": 409,
    "KILL_SWITCH_NOT_ACTIVE": 409,
}


def _order_out(order: Order) -> OrderOut:
    return OrderOut(
        order_id=order.order_id,
        client_order_id=order.client_order_id,
        account_id=order.account_id,
        deployment_id=order.deployment_id,
        instrument_id=order.instrument_id,
        side=order.side,
        order_type=order.order_type,
        time_in_force=order.time_in_force,
        quantity=order.quantity,
        limit_price=order.limit_price,
        status=order.status.value,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity,
        average_fill_price=order.average_fill_price,
        fees=order.fees,
        reject_code=order.reject_code,
        reject_reason=order.reject_reason,
        risk_decision_id=order.risk_decision_id,
        source=order.source.value,
        created_at=order.created_at,
    )


def _kill_switch_out(switch: KillSwitch) -> KillSwitchOut:
    return KillSwitchOut(
        kill_switch_id=switch.kill_switch_id,
        scope=switch.scope,
        target_id=switch.target_id,
        action=switch.action,
        reason=switch.reason,
        triggered_by=switch.triggered_by,
        triggered_at=switch.triggered_at,
        active=switch.active,
        released_by=switch.released_by,
        released_at=switch.released_at,
    )


def _problem(status: int, error: PlatformError, request: Request) -> JSONResponse:
    """RFC 9457 problem details extended with a stable code (Chapter 80.2)."""
    body = {
        "type": "about:blank",
        "title": error.code,
        "status": status,
        "detail": error.message,
        "code": error.code,
        "correlation_id": request.headers.get("X-Correlation-Id") or str(uuid.uuid4()),
    }
    if isinstance(error, ValidationError):
        body["errors"] = error.violations
    elif error.details is not None:
        body["details"] = error.details
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def create_app(platform: Platform | None = None) -> FastAPI:
    p = platform or build_paper_platform()
    app = FastAPI(title="JD Quant AI", version=__version__)
    app.state.platform = p

    @app.exception_handler(PlatformError)
    async def platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        if isinstance(exc, NotFoundError):
            status = 404
        elif isinstance(exc, ValidationError):
            status = 400
        else:
            status = _STATUS_BY_CODE.get(exc.code, 422)
        return _problem(status, exc, request)

    @app.get("/api/v1/health")
    def health() -> dict:
        return {
            "status": "OPERATIONAL",
            "version": __version__,
            "maintenance_mode": p.trading.maintenance_mode,
        }

    # ---- instruments & market data --------------------------------------------------------

    def _instrument_out(i) -> InstrumentOut:
        return InstrumentOut(
            instrument_id=i.instrument_id,
            venue=i.venue,
            symbol=i.symbol,
            asset_class=i.asset_class.value,
            base_asset=i.base_asset,
            quote_asset=i.quote_asset,
            tick_size=i.tick_size,
            lot_size=i.lot_size,
            min_quantity=i.min_quantity,
            min_notional=i.min_notional,
            status=i.status.value,
            reference_price=p.market.reference_price(i.instrument_id),
            feed_status=p.market.status(i.instrument_id).value,
        )

    @app.get("/api/v1/instruments", response_model=list[InstrumentOut])
    def list_instruments(query: str = "") -> list[InstrumentOut]:
        return [_instrument_out(i) for i in p.instruments.search(query)]

    @app.get("/api/v1/instruments/{instrument_id}", response_model=InstrumentOut)
    def get_instrument(instrument_id: str) -> InstrumentOut:
        return _instrument_out(p.instruments.get(instrument_id))

    @app.post("/api/v1/market-data/quotes", status_code=204)
    def push_quote(body: QuoteIn) -> None:
        """Inject a quote for paper trading without a live feed."""
        p.instruments.get(body.instrument_id)
        if body.bid_price >= body.ask_price:
            raise ValidationError(
                "QUOTE_INVALID", [{"field": "ask_price", "message": "must exceed bid_price"}]
            )
        p.market.on_quote(
            Quote(
                body.instrument_id,
                p.clock.now(),
                body.bid_price,
                body.bid_size,
                body.ask_price,
                body.ask_size,
            )
        )

    # ---- orders -----------------------------------------------------------------------------

    @app.post("/api/v1/orders", response_model=OrderOut, status_code=201)
    def submit_order(body: OrderIn, idempotency_key: str | None = Header(default=None)) -> OrderOut:
        p.trading.get_account(body.account_id)
        order = p.oms.submit(
            OrderRequest(
                account_id=body.account_id,
                instrument_id=body.instrument_id,
                side=body.side,
                order_type=body.order_type,
                quantity=body.quantity,
                limit_price=body.limit_price,
                stop_price=body.stop_price,
                time_in_force=body.time_in_force,
                post_only=body.post_only,
                reduce_only=body.reduce_only,
                source=OrderSource.API,
                submitter=API_USER,
                idempotency_key=idempotency_key,
                tags=body.tags,
            )
        )
        if order.status in (OrderStatus.REJECTED, OrderStatus.RISK_REJECTED):
            raise PlatformError(
                order.reject_code or "ORDER_REJECTED",
                order.reject_reason or "order rejected",
                details={"order": _order_out(order).model_dump(mode="json")},
            )
        return _order_out(order)

    @app.get("/api/v1/orders", response_model=list[OrderOut])
    def list_orders(account_id: str | None = None, working_only: bool = False) -> list[OrderOut]:
        return [_order_out(o) for o in p.oms.list_orders(account_id=account_id, working_only=working_only)]

    @app.get("/api/v1/orders/{order_id}", response_model=OrderOut)
    def get_order(order_id: str) -> OrderOut:
        return _order_out(p.oms.get(order_id))

    @app.delete("/api/v1/orders/{order_id}", response_model=OrderOut)
    def cancel_order(order_id: str) -> OrderOut:
        return _order_out(p.oms.cancel(order_id))

    @app.get("/api/v1/orders/{order_id}/events")
    def order_events(order_id: str) -> list[dict]:
        return [
            {"from": t.from_status.value, "to": t.to_status.value, "at": t.at.isoformat(), "reason": t.reason}
            for t in p.oms.get(order_id).history
        ]

    # ---- positions --------------------------------------------------------------------------

    @app.get("/api/v1/positions", response_model=list[PositionOut])
    def list_positions(account_id: str | None = None) -> list[PositionOut]:
        result = []
        for pos in p.positions.positions(account_id=account_id):
            price = p.market.reference_price(pos.instrument_id)
            result.append(
                PositionOut(
                    account_id=pos.account_id,
                    instrument_id=pos.instrument_id,
                    deployment_id=pos.deployment_id,
                    quantity=pos.quantity,
                    average_entry_price=pos.average_entry_price,
                    realized_pnl=pos.realized_pnl,
                    unrealized_pnl=pos.unrealized_pnl(price) if price is not None else None,
                    fees_paid=pos.fees_paid,
                )
            )
        return result

    # ---- kill switches ----------------------------------------------------------------------

    @app.get("/api/v1/kill-switches", response_model=list[KillSwitchOut])
    def list_kill_switches(active_only: bool = False) -> list[KillSwitchOut]:
        return [_kill_switch_out(s) for s in p.trading.kill_switches.values() if s.active or not active_only]

    @app.post("/api/v1/kill-switches", response_model=KillSwitchOut, status_code=201)
    def trigger_kill_switch(body: KillSwitchIn) -> KillSwitchOut:
        switch = p.trading.trigger_kill_switch(
            body.scope, body.action, reason=body.reason, actor=API_USER, target_id=body.target_id
        )
        return _kill_switch_out(switch)

    @app.post("/api/v1/kill-switches/{kill_switch_id}:release", response_model=KillSwitchOut)
    def release_kill_switch(kill_switch_id: str, body: KillSwitchReleaseIn) -> KillSwitchOut:
        return _kill_switch_out(
            p.trading.release_kill_switch(kill_switch_id, actor=API_USER, reason=body.reason)
        )

    # ---- strategies & backtests -------------------------------------------------------------

    @app.get("/api/v1/strategy-templates", response_model=list[StrategyTemplateOut])
    def strategy_templates() -> list[StrategyTemplateOut]:
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

    @app.post("/api/v1/backtests", response_model=BacktestOut, status_code=201)
    def create_backtest(body: BacktestIn) -> BacktestOut:
        instrument = p.instruments.get(body.instrument_id)
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

    return app


app = create_app()
