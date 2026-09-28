"""Market intelligence API: order books, flow, structure, events, scanner, options, health and replay."""

from __future__ import annotations

import asyncio
import json
import queue
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from jdquant.api.deps import ctx, require
from jdquant.core.errors import NotFoundError
from jdquant.intelligence.events import KINDS
from jdquant.intelligence.service import MarketIntelligence, book_dict
from jdquant.security.identity import Principal

router = APIRouter(prefix="/api/v1/intelligence", tags=["market intelligence"])


def _intel(request: Request) -> MarketIntelligence:
    return ctx(request).services["intelligence"]


class RecordingIn(BaseModel):
    enabled: bool | None = None
    acknowledge: bool = False
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class SettingsIn(BaseModel):
    watch: list[str] | None = None
    scanner_universe: list[str] | None = None
    scanner_enabled: bool | None = None
    option_underlyings: list[str] | None = None
    explain_events: bool | None = None
    explain_min_severity: str | None = None
    streaming_enabled: bool | None = None
    deep_depth: list[str] | None = None
    recording: RecordingIn | None = None


@router.get("/settings")
def get_settings(
    request: Request, principal: Principal = Depends(require("marketdata:view"))
) -> dict[str, Any]:
    intel = _intel(request)
    return {
        **intel.settings(),
        "recorder": intel.recorder.settings(),
        "chain_sources": intel.chain_sources(),
        "simulated_depth": intel.simulated is not None,
        "event_kinds": {k: {"category": v[0], "severity": v[1], "meaning": v[3]} for k, v in KINDS.items()},
    }


@router.put("/settings")
def put_settings(
    body: SettingsIn, request: Request, principal: Principal = Depends(require("marketdata:subscribe"))
) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    result = _intel(request).configure(changes, principal.user_id)
    ctx(request).audit.record(
        actor=principal.user_id, action="intelligence.configure", category="CONFIGURATION", data=changes
    )
    return result


@router.get("/dashboard")
def dashboard(request: Request, principal: Principal = Depends(require("marketdata:view"))) -> dict[str, Any]:
    return _intel(request).dashboard()


@router.get("/instruments/{instrument_id}")
def overview(
    instrument_id: str,
    request: Request,
    levels: int = Query(default=20, ge=1, le=200),
    principal: Principal = Depends(require("marketdata:view")),
) -> dict[str, Any]:
    return _intel(request).overview(instrument_id, levels)


@router.post("/instruments/{instrument_id}/analyze")
def analyze(
    instrument_id: str, request: Request, principal: Principal = Depends(require("marketdata:view"))
) -> dict[str, Any]:
    return _intel(request).analyze(instrument_id)


@router.get("/instruments/{instrument_id}/book")
def book(
    instrument_id: str,
    request: Request,
    source: str | None = None,
    levels: int = Query(default=20, ge=1, le=200),
    principal: Principal = Depends(require("marketdata:view")),
) -> dict[str, Any]:
    intel = _intel(request)
    snapshot = intel.hub.book(instrument_id, source)
    if snapshot is None:
        raise NotFoundError(
            "NO_BOOK", f"no order book for {instrument_id} yet; add it to the depth watchlist"
        )
    return {
        "instrument_id": instrument_id,
        "book": book_dict(snapshot, levels),
        "sources": sorted(intel.hub.books(instrument_id)),
        "walls": intel.orderbook.walls(instrument_id)["active"],
    }


@router.get("/events")
def events(
    request: Request,
    instrument_id: str | None = None,
    kinds: str = "",
    category: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    text: str | None = Query(default=None, max_length=100),
    min_severity: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require("marketdata:view")),
) -> list[dict[str, Any]]:
    found = _intel(request).event_store.search(
        instrument_id=instrument_id,
        kinds=[k for k in kinds.split(",") if k] or None,
        category=category,
        since=since,
        until=until,
        text=text,
        min_severity=min_severity,
        limit=limit,
    )
    return [e.to_dict() for e in found]


@router.get("/events/stream")
async def event_stream(
    request: Request,
    instrument_id: str | None = None,
    max_events: int = Query(default=0, ge=0),
    principal: Principal = Depends(require("marketdata:view")),
) -> StreamingResponse:
    """New market events as they are detected (`event: market`)."""
    engine = _intel(request).events
    subscription = engine.subscribe()

    async def stream():
        sent, idle = 0, 0
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                delivered = False
                while True:
                    try:
                        event = subscription.get_nowait()
                    except queue.Empty:
                        break
                    if instrument_id and event["instrument_id"] != instrument_id:
                        continue
                    yield f"event: market\ndata: {json.dumps(event, default=str)}\n\n"
                    delivered, sent = True, sent + 1
                    if max_events and sent >= max_events:
                        return
                if not delivered:
                    idle += 1
                    if idle >= 60:
                        idle = 0
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(0.25)
        finally:
            engine.unsubscribe(subscription)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/events/{event_id}")
def event(event_id: str, request: Request, principal: Principal = Depends(require("marketdata:view"))):
    found = _intel(request).event_store.get(event_id)
    if found is None:
        raise NotFoundError("EVENT_NOT_FOUND", f"unknown event {event_id}")
    return found.to_dict()


@router.get("/events/{event_id}/graph")
def event_graph(
    event_id: str,
    request: Request,
    depth: int = Query(default=4, ge=1, le=8),
    principal: Principal = Depends(require("marketdata:view")),
) -> dict[str, Any]:
    intel = _intel(request)
    if intel.event_store.get(event_id) is None:
        raise NotFoundError("EVENT_NOT_FOUND", f"unknown event {event_id}")
    return intel.event_store.graph(event_id, depth)


@router.post("/events/{event_id}/explain")
def explain(event_id: str, request: Request, principal: Principal = Depends(require("marketdata:view"))):
    return _intel(request).explain(event_id)


@router.get("/scanner")
def scanner(request: Request, principal: Principal = Depends(require("marketdata:view"))) -> dict[str, Any]:
    intel = _intel(request)
    return intel.scanner.last or {"results": [], "universe": len(intel.scanner_universe), "at": None}


@router.post("/scanner/run")
def run_scanner(
    request: Request, principal: Principal = Depends(require("marketdata:view"))
) -> dict[str, Any]:
    intel = _intel(request)
    universe = intel.scanner_universe or intel.watch
    return intel.scanner.run(universe)


@router.get("/options/{underlying}")
def options(
    underlying: str,
    request: Request,
    expiry: date | None = None,
    refresh: bool = False,
    principal: Principal = Depends(require("marketdata:view")),
) -> dict[str, Any]:
    intel = _intel(request)
    if refresh or expiry is not None:
        return intel.refresh_chain(underlying, expiry)
    return intel.chain(underlying)


@router.get("/futures/{instrument_id}")
def futures(instrument_id: str, request: Request, principal: Principal = Depends(require("marketdata:view"))):
    view = _intel(request).futures_view(instrument_id)
    if view is None:
        raise NotFoundError("NOT_A_FUTURE", f"{instrument_id} is not a futures contract")
    return view


@router.get("/health")
def health(request: Request, principal: Principal = Depends(require("marketdata:view"))) -> dict[str, Any]:
    return _intel(request).health()


@router.get("/quality")
def quality(
    request: Request, principal: Principal = Depends(require("marketdata:view"))
) -> list[dict[str, Any]]:
    return _intel(request).quality.report()


@router.get("/recordings")
def recordings(request: Request, principal: Principal = Depends(require("marketdata:view"))):
    intel = _intel(request)
    return {"settings": intel.recorder.settings(), "coverage": intel.recorder.coverage()}
