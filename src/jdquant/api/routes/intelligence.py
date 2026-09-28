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


# ---- news and corporate events ------------------------------------------------------------------------


class NewsIn(BaseModel):
    headline: str = Field(min_length=3, max_length=500)
    url: str = Field(default="", max_length=1000)
    summary: str = Field(default="", max_length=2000)
    source: str = Field(default="manual", max_length=100)
    at: datetime | None = None
    instruments: list[str] = Field(default_factory=list)


class NewsSettingsIn(BaseModel):
    feeds: list[str] | None = None
    block_mode: str | None = None


class CorporateIn(BaseModel):
    instrument_id: str
    kind: str
    day: date
    details: str = Field(default="", max_length=500)


class CsvIn(BaseModel):
    csv: str = Field(max_length=200_000)


@router.get("/news")
def news(
    request: Request,
    instrument_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require("marketdata:view")),
):
    desk = _intel(request).news
    return {"settings": desk.settings(), "items": desk.list(instrument_id, limit)}


@router.post("/news", status_code=201)
def add_news(body: NewsIn, request: Request, principal: Principal = Depends(require("marketdata:import"))):
    item = _intel(request).news.ingest(
        body.headline,
        source=body.source,
        url=body.url,
        summary=body.summary,
        at=body.at,
        instruments=body.instruments,
    )
    return item.to_dict()


@router.put("/news/settings")
def news_settings(
    body: NewsSettingsIn, request: Request, principal: Principal = Depends(require("marketdata:subscribe"))
):
    result = _intel(request).news.configure(feeds=body.feeds, block_mode=body.block_mode)
    ctx(request).audit.record(
        actor=principal.user_id,
        action="intelligence.news_settings",
        category="CONFIGURATION",
        data=body.model_dump(exclude_none=True),
    )
    return result


@router.post("/news/poll")
def poll_news(request: Request, principal: Principal = Depends(require("marketdata:subscribe"))):
    return {"added": _intel(request).news.poll(), "settings": _intel(request).news.settings()}


@router.get("/news/{news_id}/reaction")
def news_reaction(news_id: str, request: Request, principal: Principal = Depends(require("marketdata:view"))):
    return _intel(request).news.reaction(news_id)


@router.get("/corporate-events")
def corporate(
    request: Request,
    days: int = Query(default=30, ge=0, le=365),
    instrument_id: str | None = None,
    principal: Principal = Depends(require("marketdata:view")),
):
    return _intel(request).news.corporate(days=days, instrument_id=instrument_id)


@router.post("/corporate-events", status_code=201)
def add_corporate(
    body: CorporateIn, request: Request, principal: Principal = Depends(require("marketdata:import"))
):
    return (
        _intel(request)
        .news.add_corporate(body.instrument_id, body.kind.upper(), body.day, body.details)
        .to_dict()
    )


@router.post("/corporate-events/import")
def import_corporate(
    body: CsvIn, request: Request, principal: Principal = Depends(require("marketdata:import"))
):
    return _intel(request).news.import_csv(body.csv)


@router.delete("/corporate-events/{event_id}", status_code=204)
def delete_corporate(
    event_id: str, request: Request, principal: Principal = Depends(require("marketdata:import"))
):
    _intel(request).news.delete_corporate(event_id)


# ---- replay -------------------------------------------------------------------------------------------


class ReplayIn(BaseModel):
    instrument_id: str
    start: datetime
    end: datetime


@router.post("/replays", status_code=201)
def create_replay(
    body: ReplayIn, request: Request, principal: Principal = Depends(require("marketdata:view"))
):
    return _intel(request).replay.create(body.instrument_id, body.start, body.end)


@router.get("/replays/{replay_id}")
def replay_info(replay_id: str, request: Request, principal: Principal = Depends(require("marketdata:view"))):
    return _intel(request).replay.get(replay_id).info()


@router.get("/replays/{replay_id}/frame")
def replay_frame(
    replay_id: str,
    at: datetime,
    request: Request,
    levels: int = Query(default=20, ge=1, le=200),
    principal: Principal = Depends(require("marketdata:view")),
):
    return _intel(request).replay.get(replay_id).frame(at, levels)


@router.delete("/replays/{replay_id}", status_code=204)
def delete_replay(
    replay_id: str, request: Request, principal: Principal = Depends(require("marketdata:view"))
):
    _intel(request).replay.delete(replay_id)
