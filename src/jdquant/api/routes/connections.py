"""Venue connections (Chapter 45). Credentials are write-only (CON-080)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from jdquant.api.deps import ctx, locked, require
from jdquant.api.schemas import ConnectionIn, ConnectionOut, CredentialsIn, WatchlistIn
from jdquant.connectivity.base import Environment
from jdquant.connectivity.connections import Connection, ConnectionManager
from jdquant.core.errors import ValidationError
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
    )


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
