"""Venue connections (Chapter 45). Credentials are write-only (CON-080)."""

from __future__ import annotations

import os
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from jdquant.api.deps import ctx, locked, require
from jdquant.api.schemas import BrokerLoginOut, ConnectionIn, ConnectionOut, CredentialsIn, PinIn, WatchlistIn
from jdquant.connectivity.base import Environment
from jdquant.connectivity.connections import ADAPTERS, Connection, ConnectionManager, markets_of
from jdquant.core.errors import PlatformError, ValidationError
from jdquant.security.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["connections"])


def _manager(request: Request) -> ConnectionManager:
    return ctx(request).services["connections"]


def connection_out(c: Connection) -> ConnectionOut:
    return ConnectionOut(
        connection_id=c.connection_id,
        name=c.name,
        venue=c.venue,
        environment=c.environment.value,
        account_id=c.account_id,
        has_credentials=c.has_credentials,
        status=c.status.value,
        last_error=c.last_error,
        last_tested_at=c.last_tested_at,
        clock_offset_ms=c.clock_offset_ms,
        instrument_count=c.instrument_count,
        watchlist=c.watchlist,
        settings=c.settings,
        requires_login=bool(getattr(ADAPTERS.get(c.venue), "requires_login", False)),
        session_expires_at=c.session_expires_at,
        markets=list(markets_of(c.venue)),
    )


CALLBACK_PATH = "/api/v1/connections/oauth/callback"


def redirect_uri(request: Request) -> str:
    """Where the broker sends the user back; must match the redirect URL registered with the broker app."""
    base = os.environ.get("JDQ_PUBLIC_URL") or str(request.base_url)
    return base.rstrip("/") + CALLBACK_PATH


def _audit(request: Request, principal: Principal, action: str, target: str, **data) -> None:
    ctx(request).audit.record(
        actor=principal.user_id, action=action, category="CONFIGURATION", target=target, data=data
    )


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(request: Request, principal: Principal = Depends(require("connection:view"))):
    return [connection_out(c) for c in _manager(request).connections.values()]


@router.post("/connections", response_model=ConnectionOut, status_code=201)
@locked
def create_connection(
    body: ConnectionIn, request: Request, principal: Principal = Depends(require("connection:create"))
):
    try:
        environment = Environment(body.environment)
    except ValueError:
        raise ValidationError(
            "ENVIRONMENT_INVALID", [{"field": "environment", "message": "PRODUCTION or TESTNET"}]
        ) from None
    connection = _manager(request).create(
        name=body.name,
        venue=body.venue,
        environment=environment,
        actor=principal.user_id,
        api_key=body.api_key or None,
        api_secret=body.api_secret or None,
        base_currency=body.base_currency,
        settings=body.settings,
    )
    _audit(
        request,
        principal,
        "connection.create",
        connection.connection_id,
        venue=connection.venue,
        environment=environment.value,
        credentials=connection.has_credentials,
    )
    return connection_out(connection)


@router.post("/connections/{connection_id}:test", response_model=ConnectionOut)
def test_connection(
    connection_id: str, request: Request, principal: Principal = Depends(require("connection:update"))
):
    return connection_out(_manager(request).test(connection_id))


@router.post("/connections/{connection_id}:rotate-credentials", response_model=ConnectionOut)
@locked
def rotate(
    connection_id: str,
    body: CredentialsIn,
    request: Request,
    principal: Principal = Depends(require("connection:rotate")),
):
    connection = _manager(request).rotate(connection_id, body.api_key, body.api_secret)
    _audit(request, principal, "connection.rotate", connection_id)
    return connection_out(connection)


@router.post("/connections/{connection_id}:disable", response_model=ConnectionOut)
@locked
def disable(
    connection_id: str, request: Request, principal: Principal = Depends(require("connection:update"))
):
    connection = _manager(request).disable(connection_id)
    _audit(request, principal, "connection.disable", connection_id)
    return connection_out(connection)


@router.put("/connections/{connection_id}/watchlist", response_model=ConnectionOut)
@locked
def set_watchlist(
    connection_id: str,
    body: WatchlistIn,
    request: Request,
    principal: Principal = Depends(require("connection:update")),
):
    return connection_out(_manager(request).set_watchlist(connection_id, body.instruments))


@router.get("/accounts/{account_id}/balances")
def balances(account_id: str, request: Request, principal: Principal = Depends(require("account:view"))):
    ctx(request).platform.trading.get_account(account_id)
    adapter = _manager(request).adapter_for_account(account_id)
    if adapter is None:
        return []
    return [
        {"asset": b.asset, "free": str(b.free), "locked": str(b.locked), "total": str(b.total)}
        for b in adapter.fetch_balances()
    ]


@router.post("/connections/{connection_id}:login", response_model=BrokerLoginOut)
def begin_login(
    connection_id: str, request: Request, principal: Principal = Depends(require("connection:update"))
):
    uri = redirect_uri(request)
    url = _manager(request).begin_login(connection_id, uri)
    _audit(request, principal, "connection.login.start", connection_id)
    return BrokerLoginOut(login_url=url, redirect_uri=uri)


@router.get(CALLBACK_PATH.removeprefix("/api/v1"), include_in_schema=False)
def login_callback(
    request: Request,
    state: str = "",
    auth_code: str = "",  # Fyers
    request_token: str = "",  # Zerodha Kite
    code: str = "",  # Upstox
    s: str = "",
    status: str = "",
) -> RedirectResponse:
    """The broker redirects the browser here. The one-time `state` proves which login this completes;
    session cookies are not sent on this cross-site redirect, so they are not relied on."""
    c = ctx(request)
    auth_code = auth_code or request_token or code
    try:
        if (s and s != "ok") or (status and status != "success") or not auth_code:
            raise PlatformError("LOGIN_FAILED", "the broker did not return an authorization code")
        with c.platform.lock:
            connection = _manager(request).complete_login(state, auth_code)
    except PlatformError as exc:
        c.audit.record(actor="broker-login", action="connection.login", category="AUTHENTICATION",
                       outcome="FAILURE", data={"code": exc.code})  # fmt: skip
        query = urlencode({"login": "failed", "reason": exc.message})
        return RedirectResponse(f"/connections?{query}", status_code=303)
    c.audit.record(
        actor=connection.created_by,
        action="connection.login",
        category="AUTHENTICATION",
        target=connection.connection_id,
    )
    return RedirectResponse("/connections?login=ok", status_code=303)


@router.put("/connections/{connection_id}/pin", response_model=ConnectionOut)
@locked
def set_pin(
    connection_id: str,
    body: PinIn,
    request: Request,
    principal: Principal = Depends(require("connection:rotate")),
):
    """Store the broker PIN (encrypted) so expired sessions renew without a manual sign-in."""
    connection = _manager(request).set_pin(connection_id, body.pin)
    _audit(request, principal, "connection.pin." + ("set" if body.pin else "clear"), connection_id)
    return connection_out(connection)
