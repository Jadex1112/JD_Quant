"""Chart data: candle history and a live quote stream (server-sent events)."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from jdquant.api.deps import ctx, require
from jdquant.marketdata.live import LiveMarket, candle_dict
from jdquant.security.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["market data"])
log = logging.getLogger(__name__)


def _live(request: Request) -> LiveMarket:
    return ctx(request).services["live"]


@router.get("/market-data/candles")
def candles(
    request: Request,
    instrument_id: str,
    interval_seconds: int = Query(default=60, ge=60, le=86400),
    limit: int = Query(default=300, ge=10, le=2000),
    principal: Principal = Depends(require("marketdata:view")),
) -> dict[str, Any]:
    """Broker history where a live source exists, completed with bars built from live quotes."""
    c = ctx(request)
    instrument = c.platform.instruments.get(instrument_id)
    live = _live(request)
    bars: list[dict[str, Any]] = []
    source = c.services["connections"].data_source_for(instrument_id)
    origin = "quotes"
    if source is not None:
        try:
            bars = [candle_dict(k) for k in source.fetch_candles(instrument, interval_seconds, limit)]
            origin = "broker"
        except Exception as exc:  # history is optional: fall back to bars built from quotes
            log.warning("chart history for %s unavailable: %s", instrument_id, exc)
    last = bars[-1]["open_ts"] if bars else None
    bars += [
        b for b in live.candles(instrument_id, interval_seconds, limit) if last is None or b["open_ts"] > last
    ]
    return {
        "instrument_id": instrument_id,
        "interval_seconds": interval_seconds,
        "origin": origin,
        "simulated": instrument_id in live.simulated,
        "candles": [
            {"time": int(b["open_ts"].timestamp()), **{k: b[k] for k in ("open", "high", "low", "close")}}
            for b in bars[-limit:]
        ],
    }


@router.get("/market-data/stream")
async def stream(
    request: Request,
    instruments: str = "",
    max_events: int = Query(default=0, ge=0, description="stop after this many quotes (0: until closed)"),
    principal: Principal = Depends(require("marketdata:view")),
) -> StreamingResponse:
    """Every quote for the requested instruments as it arrives (`event: quote`)."""
    live = _live(request)
    wanted = {i for i in instruments.split(",") if i}
    subscription = live.subscribe()

    async def events():
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
                    if wanted and event["instrument_id"] not in wanted:
                        continue
                    yield f"event: quote\ndata: {json.dumps(event)}\n\n"
                    delivered, sent = True, sent + 1
                    if max_events and sent >= max_events:
                        return
                if not delivered:
                    idle += 1
                    if idle >= 60:  # a comment every ~15 s keeps proxies from closing the stream
                        idle = 0
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(0.25)
        finally:
            live.unsubscribe(subscription)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
