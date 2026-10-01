"""Live chart data: minute candles from quotes, history endpoint, SSE stream, simulated feed."""

import json
from datetime import timedelta
from decimal import Decimal

from conftest import BTC, T0, advance, login_client, set_quote

from jdquant.marketdata.live import LiveMarket, SimulatedFeed
from jdquant.marketdata.records import Quote


def q(price, at, instrument=BTC):
    return Quote(instrument, at, Decimal(price) - 1, Decimal(1), Decimal(price) + 1, Decimal(1))


def test_quotes_build_minute_bars_and_aggregate():
    live = LiveMarket()
    for seconds, price in ((0, 100), (20, 105), (40, 98), (61, 101), (130, 110)):
        live.on_quote(q(price, T0 + timedelta(seconds=seconds)))
    minutes = live.candles(BTC, 60, 10)
    assert [(b["open"], b["high"], b["low"], b["close"]) for b in minutes] == [
        (100, 105, 98, 98), (101, 101, 101, 101), (110, 110, 110, 110)
    ]  # fmt: skip
    five = live.candles(BTC, 300, 10)
    assert len(five) == 1 and five[0]["high"] == 110 and five[0]["low"] == 98 and five[0]["close"] == 110


def test_subscribers_get_every_quote_and_slow_ones_do_not_block():
    live = LiveMarket()
    sub = live.subscribe()
    live.on_quote(q(100, T0))
    assert sub.get_nowait()["price"] == 100.0
    live.unsubscribe(sub)
    live.on_quote(q(101, T0))
    assert sub.empty()


def test_candles_endpoint_and_stream(platform):
    client = login_client(platform)
    set_quote(platform, BTC, "49999", "50001")
    advance(platform.clock, seconds=61)
    set_quote(platform, BTC, "50099", "50101")
    body = client.get("/api/v1/market-data/candles", params={"instrument_id": BTC}).json()
    assert body["origin"] == "quotes" and body["simulated"] is False
    assert [c["close"] for c in body["candles"]] == [50000.0, 50100.0]
    assert body["candles"][0]["time"] == int(T0.timestamp())

    import threading

    def push():
        import time

        time.sleep(0.3)
        set_quote(platform, BTC, "50199", "50201")
        set_quote(platform, "BINANCE:ETHUSDT", "2999", "3001")  # filtered out
        set_quote(platform, BTC, "50299", "50301")

    threading.Thread(target=push).start()
    with client.stream(
        "GET", "/api/v1/market-data/stream", params={"instruments": BTC, "max_events": 2}
    ) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        data = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert [d["price"] for d in data] == [50200.0, 50300.0]
    assert all(d["instrument_id"] == BTC for d in data)


def test_simulated_feed_is_labelled_and_skips_live_sources(platform):
    live = LiveMarket()
    platform.market.listeners.append(live.on_quote)
    feed = SimulatedFeed(platform, platform.market.on_quote, has_source=lambda i: i == BTC, live=live)
    feed.backfill(live, minutes=30)
    feed.tick()
    assert BTC not in live.simulated and "NSE:RELIANCE" in live.simulated
    bars = live.candles("NSE:RELIANCE", 60, 100)
    assert len(bars) >= 30
    # several prices per minute, so the demo candles have bodies and wicks rather than flat dashes
    assert sum(b["high"] > b["low"] for b in bars) > len(bars) // 2
    assert platform.market.reference_price("NSE:RELIANCE") is not None
