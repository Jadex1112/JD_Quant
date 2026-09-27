"""Market data, accounts, orders, positions, kill switches, deployments and risk (Chapters 19–28)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from jdquant.api.context import AppContext
from jdquant.api.convert import account_out, deployment_out, kill_switch_out, order_out, risk_profile_io
from jdquant.api.deps import ctx, locked, require
from jdquant.api.schemas import (
    AccountOut,
    AccountStatusIn,
    DeploymentIn,
    DeploymentOut,
    InstrumentOut,
    KillSwitchIn,
    KillSwitchOut,
    KillSwitchReleaseIn,
    OrderIn,
    OrderOut,
    PositionOut,
    QuoteIn,
    RiskProfileIO,
)
from jdquant.core.errors import PlatformError, ValidationError
from jdquant.marketdata.records import Quote
from jdquant.oms.orders import OrderRequest, OrderSource, OrderStatus
from jdquant.risk.engine import BreachAction, RiskLimit, RiskProfile, Scope
from jdquant.security.identity import Principal
from jdquant.strategy.base import validate_parameters
from jdquant.strategy.templates import TEMPLATES
from jdquant.trading.engine import AccountStatus

router = APIRouter(prefix="/api/v1", tags=["trading"])


def _instrument_out(c: AppContext, i) -> InstrumentOut:
    m = c.platform.market
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
        reference_price=m.reference_price(i.instrument_id),
        feed_status=m.status(i.instrument_id).value,
    )


def _audit(
    request: Request, principal: Principal, action: str, category: str, target: str | None = None, /, **data
):
    ctx(request).audit.record(
        actor=principal.user_id, action=action, category=category, target=target, data=data
    )


# ---- market data -------------------------------------------------------------------------------


@router.get("/instruments", response_model=list[InstrumentOut])
@locked
def list_instruments(
    request: Request, query: str = "", principal: Principal = Depends(require("marketdata:view"))
):
    c = ctx(request)
    return [_instrument_out(c, i) for i in c.platform.instruments.search(query)]


@router.get("/instruments/{instrument_id}", response_model=InstrumentOut)
@locked
def get_instrument(
    instrument_id: str, request: Request, principal: Principal = Depends(require("marketdata:view"))
):
    c = ctx(request)
    return _instrument_out(c, c.platform.instruments.get(instrument_id))


@router.post("/market-data/quotes", status_code=204)
@locked
def push_quote(
    body: QuoteIn, request: Request, principal: Principal = Depends(require("marketdata:manage_reference"))
):
    """Inject a quote for paper trading without a live feed."""
    p = ctx(request).platform
    p.instruments.get(body.instrument_id)
    if body.bid_price >= body.ask_price:
        raise ValidationError("QUOTE_INVALID", [{"field": "ask_price", "message": "must exceed bid_price"}])
    p.market.on_quote(
        Quote(body.instrument_id, p.clock.now(), body.bid_price, body.bid_size, body.ask_price, body.ask_size)
    )


# ---- accounts ----------------------------------------------------------------------------------


@router.get("/accounts", response_model=list[AccountOut])
@locked
def list_accounts(request: Request, principal: Principal = Depends(require("account:view"))):
    return [account_out(a) for a in ctx(request).platform.trading.accounts.values()]


@router.put("/accounts/{account_id}/status", response_model=AccountOut)
@locked
def set_account_status(
    account_id: str,
    body: AccountStatusIn,
    request: Request,
    principal: Principal = Depends(require("account:suspend")),
):
    try:
        status = AccountStatus(body.status)
    except ValueError:
        raise ValidationError(
            "STATUS_INVALID", [{"field": "status", "message": f"one of {list(AccountStatus)}"}]
        ) from None
    account = ctx(request).platform.trading.set_account_status(account_id, status)
    _audit(request, principal, "account.status.update", "TRADING", account_id, status=status.value)
    return account_out(account)


# ---- orders & positions ------------------------------------------------------------------------


@router.post("/orders", response_model=OrderOut, status_code=201)
@locked
def submit_order(
    body: OrderIn,
    request: Request,
    idempotency_key: str | None = Header(default=None),
    principal: Principal = Depends(require("order:create")),
):
    p = ctx(request).platform
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
            source=OrderSource.API if principal.method.value == "API_KEY" else OrderSource.MANUAL,
            submitter=principal.user_id,
            idempotency_key=idempotency_key,
            tags=body.tags,
        )
    )
    _audit(
        request,
        principal,
        "order.submit",
        "TRADING",
        order.order_id,
        status=order.status.value,
        instrument=order.instrument_id,
        side=order.side.value,
        quantity=order.quantity,
    )
    if order.status in (OrderStatus.REJECTED, OrderStatus.RISK_REJECTED):
        raise PlatformError(
            order.reject_code or "ORDER_REJECTED",
            order.reject_reason or "order rejected",
            details={"order": order_out(order).model_dump(mode="json")},
        )
    return order_out(order)


@router.get("/orders", response_model=list[OrderOut])
@locked
def list_orders(
    request: Request,
    account_id: str | None = None,
    working_only: bool = False,
    principal: Principal = Depends(require("order:view")),
):
    orders = ctx(request).platform.oms.list_orders(account_id=account_id, working_only=working_only)
    return [order_out(o) for o in sorted(orders, key=lambda o: o.created_at, reverse=True)]


@router.get("/orders/{order_id}", response_model=OrderOut)
@locked
def get_order(order_id: str, request: Request, principal: Principal = Depends(require("order:view"))):
    return order_out(ctx(request).platform.oms.get(order_id))


@router.delete("/orders/{order_id}", response_model=OrderOut)
@locked
def cancel_order(order_id: str, request: Request, principal: Principal = Depends(require("order:cancel"))):
    order = ctx(request).platform.oms.cancel(order_id)
    _audit(request, principal, "order.cancel", "TRADING", order_id)
    return order_out(order)


@router.post("/orders:cancel-all", response_model=list[OrderOut])
@locked
def cancel_all(
    request: Request,
    account_id: str | None = None,
    principal: Principal = Depends(require("order:cancel_all")),
):
    orders = ctx(request).platform.oms.cancel_all(account_id=account_id)
    _audit(request, principal, "order.cancel_all", "TRADING", account_id, count=len(orders))
    return [order_out(o) for o in orders]


@router.get("/orders/{order_id}/events")
@locked
def order_events(order_id: str, request: Request, principal: Principal = Depends(require("order:view"))):
    return [
        {"from": t.from_status.value, "to": t.to_status.value, "at": t.at.isoformat(), "reason": t.reason}
        for t in ctx(request).platform.oms.get(order_id).history
    ]


@router.get("/fills")
@locked
def list_fills(
    request: Request,
    account_id: str | None = None,
    limit: int = 200,
    principal: Principal = Depends(require("order:view")),
):
    fills = [f for f in ctx(request).platform.oms.fills if account_id is None or f.account_id == account_id]
    return [
        {
            "fill_id": f.fill_id,
            "order_id": f.order_id,
            "account_id": f.account_id,
            "instrument_id": f.instrument_id,
            "side": f.side.value,
            "price": str(f.price),
            "quantity": str(f.quantity),
            "fee": str(f.fee),
            "fee_asset": f.fee_asset,
            "liquidity": f.liquidity.value,
            "exchange_ts": f.exchange_ts.isoformat(),
        }
        for f in reversed(fills[-limit:])
    ]


@router.get("/positions", response_model=list[PositionOut])
@locked
def list_positions(
    request: Request, account_id: str | None = None, principal: Principal = Depends(require("position:view"))
):
    p = ctx(request).platform
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


# ---- kill switches -----------------------------------------------------------------------------


@router.get("/kill-switches", response_model=list[KillSwitchOut])
@locked
def list_kill_switches(
    request: Request, active_only: bool = False, principal: Principal = Depends(require("killswitch:view"))
):
    switches = ctx(request).platform.trading.kill_switches.values()
    return [kill_switch_out(s) for s in switches if s.active or not active_only]


@router.post("/kill-switches", response_model=KillSwitchOut, status_code=201)
@locked
def trigger_kill_switch(
    body: KillSwitchIn, request: Request, principal: Principal = Depends(require("killswitch:trigger"))
):
    switch = ctx(request).platform.trading.trigger_kill_switch(
        body.scope, body.action, reason=body.reason, actor=principal.user_id, target_id=body.target_id
    )
    _audit(
        request,
        principal,
        "killswitch.trigger",
        "TRADING",
        switch.kill_switch_id,
        scope=body.scope.value,
        action=body.action.value,
        reason=body.reason,
    )
    return kill_switch_out(switch)


@router.post("/kill-switches/{kill_switch_id}:release", response_model=KillSwitchOut)
@locked
def release_kill_switch(
    kill_switch_id: str,
    body: KillSwitchReleaseIn,
    request: Request,
    principal: Principal = Depends(require("killswitch:release")),
):
    switch = ctx(request).platform.trading.release_kill_switch(
        kill_switch_id, actor=principal.user_id, reason=body.reason
    )
    _audit(request, principal, "killswitch.release", "TRADING", kill_switch_id, reason=body.reason)
    return kill_switch_out(switch)


# ---- deployments -------------------------------------------------------------------------------


@router.get("/deployments", response_model=list[DeploymentOut])
@locked
def list_deployments(request: Request, principal: Principal = Depends(require("deployment:view"))):
    return [deployment_out(d) for d in ctx(request).platform.trading.deployments.values()]


@router.post("/deployments", response_model=DeploymentOut, status_code=201)
@locked
def create_deployment(
    body: DeploymentIn, request: Request, principal: Principal = Depends(require("deployment:create"))
):
    c = ctx(request)
    strategy = TEMPLATES.get(body.strategy)
    if strategy is None:
        raise ValidationError(
            "STRATEGY_NOT_FOUND", [{"field": "strategy", "message": f"unknown {body.strategy}"}]
        )
    params = validate_parameters(strategy.parameters, body.parameters)
    for instrument_id in body.instruments:
        c.platform.instruments.get(instrument_id)
    deployment = c.platform.trading.create_deployment(
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        account_id=body.account_id,
        parameters={k: v if isinstance(v, bool | int) else str(v) for k, v in params.items()},
        instruments=body.instruments,
        created_by=principal.user_id,
        bar_interval_seconds=body.interval_seconds,
    )
    _audit(
        request, principal, "deployment.create", "TRADING", deployment.deployment_id, strategy=strategy.name
    )
    return deployment_out(deployment)


_DEPLOYMENT_ACTIONS = {
    "approve": "deployment:approve",
    "start": "deployment:start",
    "pause": "deployment:pause",
    "resume": "deployment:start",
    "stop": "deployment:stop",
    "flatten": "deployment:flatten",
    "retire": "deployment:retire",
}


def _deployment_action(action: str):
    @locked
    def handler(
        deployment_id: str,
        request: Request,
        principal: Principal = Depends(require(_DEPLOYMENT_ACTIONS[action])),
    ) -> DeploymentOut:
        c = ctx(request)
        trading = c.platform.trading
        if action == "approve":
            deployment = trading.approve(deployment_id, principal.user_id)
        else:
            deployment = getattr(trading, action)(deployment_id)
        runner = c.services.get("runner")
        if runner is not None:
            runner.sync(deployment)
        _audit(request, principal, f"deployment.{action}", "TRADING", deployment_id)
        return deployment_out(deployment)

    handler.__name__ = f"deployment_{action}"
    return handler


for _action in _DEPLOYMENT_ACTIONS:
    router.add_api_route(
        f"/deployments/{{deployment_id}}:{_action}",
        _deployment_action(_action),
        methods=["POST"],
        response_model=DeploymentOut,
    )


# ---- risk profiles -----------------------------------------------------------------------------


@router.get("/risk-profiles", response_model=list[RiskProfileIO])
@locked
def list_risk_profiles(request: Request, principal: Principal = Depends(require("risk.profile:view"))):
    return [risk_profile_io(p) for p in ctx(request).platform.risk.profiles]


@router.put("/risk-profiles", response_model=list[RiskProfileIO])
@locked
def replace_risk_profiles(
    body: list[RiskProfileIO], request: Request, principal: Principal = Depends(require("risk.limit:approve"))
):
    """Replace all profiles; loosening limits requires risk.limit:approve (FR-27003)."""
    profiles = [
        RiskProfile(
            name=p.name,
            scope=Scope(p.scope),
            target_id=p.target_id,
            limits=[RiskLimit(lim.limit_type, lim.threshold, BreachAction(lim.action)) for lim in p.limits],
            restricted_instruments=set(p.restricted_instruments),
            active=p.active,
        )
        for p in body
    ]
    risk = ctx(request).platform.risk
    before = [risk_profile_io(p).model_dump(mode="json") for p in risk.profiles]
    risk.set_profiles(profiles)
    _audit(
        request,
        principal,
        "risk.profiles.replace",
        "RISK",
        None,
        before=before,
        after=[p.model_dump(mode="json") for p in body],
    )
    return [risk_profile_io(p) for p in risk.profiles]


@router.get("/risk/status")
@locked
def risk_status(
    request: Request, account_id: str, principal: Principal = Depends(require("risk.profile:view"))
):
    risk = ctx(request).platform.risk
    return {
        "account_id": account_id,
        "daily_pnl": str(risk.daily_pnl(account_id)),
        "reduce_only": account_id in risk.reduce_only_accounts,
    }
