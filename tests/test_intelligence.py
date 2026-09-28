"""The market intelligence service end to end: simulated books -> engines -> events -> API."""

import random
from datetime import timedelta

import pytest
from conftest import OWNER, T0
from fastapi.testclient import TestClient

from jdquant.api.app import create_app
from jdquant.api.context import Settings
from jdquant.core.clock import SimulatedClock
from jdquant.intelligence.service import MarketIntelligence
from jdquant.marketdata.book import BookSnapshot, Level
from jdquant.marketdata.recorder import MarketStore
from jdquant.persistence.store import Store
from jdquant.platform import build_paper_platform

RELIANCE = "NSE:RELIANCE"
OPEN = T0.replace(hour=3, minute=45)  # 09:15 IST


def run(intel: MarketIntelligence, clock: SimulatedClock, seconds: int) -> None:
    for _ in range(seconds):
        clock.set(clock.now() + timedelta(seconds=1))
        intel.simulated.tick()


@pytest.fixture
def intel():
    clock = SimulatedClock(OPEN)
    platform = build_paper_platform(clock)
    service = MarketIntelligence(
        platform,
        Store(":memory:"),
        MarketStore(":memory:"),
        forward_quote=platform.market.on_quote,
        simulated_depth=True,
    )
    service.simulated._rng = random.Random(11)
    service.configure({"watch": [RELIANCE]}, "u1")
    return service, clock, platform


def test_simulated_books_flow_through_every_engine(intel):
    service, clock, platform = intel
    run(service, clock, 900)
    service.tick(force=True)
    books = service.hub.books(RELIANCE)
    assert set(books) == {"SIMULATED"} and books["SIMULATED"].capacity == 20
    assert platform.market.quote(RELIANCE) is not None  # the trading cache got prices from the hub
    flow = service.flow.snapshot(RELIANCE)
    assert flow["day_volume"] > 0 and len(flow["bars"]) >= 10
    kinds = {e.kind for e in service.events.recent(RELIANCE, 500)}
    assert "WALL_DETECTED" in kinds
    assert kinds & {"WALL_WITHDRAWN", "WALL_CONSUMED", "WALL_PARTIAL"}
    overview = service.overview(RELIANCE)
    assert overview["simulated"] is True and overview["walls"]["history"]
    analysis = overview["analysis"]
    assert analysis["vwap"]["vwap"] > 0 and analysis["regime"]["state"]
    assert analysis["timeframes"][0]["timeframe"] == "D"
    assert (
        overview["execution_estimate"]["buy"]["average_price"]
        >= overview["books"]["SIMULATED"]["asks"][0]["price"]
    )
    dash = service.dashboard()
    assert dash["instruments"][0]["instrument_id"] == RELIANCE


def test_withdrawal_event_graph_and_template_explanation(intel):
    service, clock, _ = intel
    run(service, clock, 1500)
    withdrawn = service.event_store.search(
        instrument_id=RELIANCE, kinds=["WALL_WITHDRAWN", "WALL_PARTIAL", "WALL_CONSUMED"]
    )
    assert withdrawn
    event = withdrawn[0]
    graph = service.event_store.graph(event.event_id)
    assert any(n["event_id"] == event.event_id for n in graph["nodes"])
    explained = service.explain(event.event_id)
    assert explained["by"] == "template" and "manipulation" in explained["explanation"]
    assert service.event_store.get(event.event_id).explanation == explained["explanation"]


def test_recording_requires_acknowledgement_and_replays_books(intel):
    service, clock, _ = intel
    with pytest.raises(Exception, match="acknowledge"):
        service.configure({"recording": {"enabled": True}}, "u1")
    settings = service.configure({"recording": {"enabled": True, "acknowledge": True}}, "u1")
    assert settings["recording"]["enabled"] and settings["recording"]["acknowledged_by"] == "u1"
    run(service, clock, 30)
    service.recorder.flush()
    coverage = service.recorder.coverage()
    assert coverage[0]["instrument_id"] == RELIANCE and coverage[0]["books"] >= 25
    back = service.recorder.books_between(RELIANCE, OPEN, clock.now())
    assert back[-1].bids == service.hub.book(RELIANCE).bids


def test_data_quality_classifies_timing_and_discrepancy():
    clock = SimulatedClock(OPEN)
    service = MarketIntelligence(build_paper_platform(clock), Store(":memory:"), MarketStore(":memory:"))

    def book(source, bid, t):
        at = OPEN + timedelta(seconds=t)
        return BookSnapshot(RELIANCE, source, at, at, (Level(bid, 10, 1),), (Level(bid + 0.1, 10, 1),))

    service.hub.publish(book("DHAN", 1400.0, 0))
    service.hub.publish(book("FYERS", 1400.0, 0.2))
    assert service.quality.report(RELIANCE)[0]["state"] == "AGREE"
    service.hub.publish(book("DHAN", 1401.0, 1))
    service.hub.publish(book("FYERS", 1400.0, 1.1))  # FYERS still shows DHAN's price of a second ago
    assert service.quality.report(RELIANCE)[0]["state"] == "TIMING"
    for t in range(2, 12):
        service.hub.publish(book("DHAN", 1405.0, t))
        service.hub.publish(book("FYERS", 1402.0, t + 0.1))
    assert service.quality.report(RELIANCE)[0]["state"] == "DISCREPANCY"
    assert service.event_store.search(kinds=["DATA_DISCREPANCY"])


@pytest.fixture
def client():
    clock = SimulatedClock(OPEN)
    platform = build_paper_platform(clock)
    app = create_app(platform, settings=Settings(enforce_mfa_for_privileged=False, demo_feed=True))
    c = TestClient(app)
    assert c.post("/api/v1/setup", json=OWNER).status_code == 201
    token = c.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    c.cookies.clear()
    c.headers["Authorization"] = f"Bearer {token.json()['token']}"
    return c, app.state.ctx.services["intelligence"], clock


def test_api_watch_overview_events_and_options(client):
    c, service, clock = client
    service.simulated._rng = random.Random(5)
    assert c.put("/api/v1/intelligence/settings", json={"watch": ["NSE:NOPE"]}).status_code == 400
    r = c.put("/api/v1/intelligence/settings", json={"watch": [RELIANCE], "option_underlyings": ["NIFTY"]})
    assert r.status_code == 200 and r.json()["watch"] == [RELIANCE]
    run(service, clock, 600)
    service.tick(force=True)
    overview = c.get(f"/api/v1/intelligence/instruments/{RELIANCE}").json()
    assert overview["books"]["SIMULATED"]["bids"] and overview["flow"]["estimated"] is True
    book = c.get(f"/api/v1/intelligence/instruments/{RELIANCE}/book", params={"levels": 5}).json()
    assert len(book["book"]["bids"]) == 5
    events = c.get("/api/v1/intelligence/events", params={"instrument_id": RELIANCE}).json()
    assert events and all(e["instrument_id"] == RELIANCE for e in events)
    first = events[0]["event_id"]
    assert c.get(f"/api/v1/intelligence/events/{first}/graph").status_code == 200
    assert "explanation" in c.post(f"/api/v1/intelligence/events/{first}/explain").json()
    chain = c.get("/api/v1/intelligence/options/NIFTY").json()
    assert chain["simulated"] is True and chain["pcr_oi"] > 0 and chain["rows"]
    health = c.get("/api/v1/intelligence/health").json()
    assert any(f["source"] == "SIMULATED" for f in health["feeds"])
    dash = c.get("/api/v1/intelligence/dashboard").json()
    assert dash["instruments"] and "events_last_hour" in dash
    scan = c.post("/api/v1/intelligence/scanner/run").json()
    assert scan["universe"] == 1
    settings = c.get("/api/v1/intelligence/settings").json()
    assert "WALL_WITHDRAWN" in settings["event_kinds"] and settings["simulated_depth"] is True


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Announcements</title>
<item><title>RELIANCE board approves bonus issue</title><link>https://example.com/a1</link>
<pubDate>Mon, 05 Jan 2026 04:10:00 GMT</pubDate>
<description>&lt;p&gt;Board meeting outcome&lt;/p&gt;</description></item>
<item><title>Unrelated market wrap</title><link>https://example.com/a2</link></item>
</channel></rss>"""


def test_news_matching_reaction_and_feed_polling(intel):
    import httpx

    service, clock, _ = intel
    run(service, clock, 600)
    item = service.news.ingest("Reliance: RELIANCE wins large order", at=clock.now() - timedelta(minutes=5))
    assert item.instruments == [RELIANCE]
    assert service.event_store.search(kinds=["NEWS"], instrument_id=RELIANCE)
    reaction = service.news.reaction(item.news_id)["reactions"][0]
    assert reaction["prices"]["0"]["price"] and "+1m" in reaction["prices"]
    assert service.news.ingest("Reliance: RELIANCE wins large order").news_id == item.news_id  # de-duplicated

    service.news._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=RSS)))
    service.news.configure(feeds=["https://example.com/rss"])
    assert service.news.poll() == 2 and service.news.poll() == 0
    listed = service.news.list(RELIANCE)
    assert any("bonus" in n["headline"] for n in listed)


def test_corporate_events_import_and_raise(intel):
    service, clock, _ = intel
    today = clock.now().date()
    result = service.news.import_csv(
        f"symbol,date,kind,details\nRELIANCE,{today},RESULTS,Q2 results\nNOPE,{today},DIVIDEND,\n"
    )
    assert result["added"] == 1 and "unknown symbol NOPE" in result["errors"][0]
    assert service.news.upcoming_for(RELIANCE)["kind"] == "RESULTS"
    service.news.raise_due()
    service.news.raise_due()
    assert len(service.event_store.search(kinds=["CORPORATE_EVENT"])) == 1


def test_replay_runs_recorded_books_through_fresh_engines(intel):
    service, clock, _ = intel
    service.configure({"recording": {"enabled": True, "acknowledge": True}}, "u1")
    start = clock.now()
    run(service, clock, 900)
    service.recorder.flush()
    info = service.replay.create(RELIANCE, start, clock.now())
    assert info["books"] >= 800 and info["sources"] == ["SIMULATED"] and info["recorded_events"]
    session = service.replay.get(info["replay_id"])
    late = session.frame(clock.now())
    assert late["done"] and late["books"]["SIMULATED"]["bids"] and late["events"]
    early = session.frame(start + timedelta(seconds=60))
    assert early["position"] < late["position"] and not early["done"]
    assert all(e["at"] <= (start + timedelta(seconds=60)).isoformat() for e in early["recorded_events"])


def test_futures_view_shows_calendar_spreads(intel):
    from decimal import Decimal

    from jdquant.marketdata.instruments import AssetClass, Instrument

    service, clock, platform = intel

    def future(symbol, days):
        return Instrument(
            "MCX",
            symbol,
            AssetClass.COMMODITY,
            "GOLDM",
            "INR",
            Decimal(1),
            Decimal(1),
            Decimal(1),
            contract_multiplier=Decimal(10),
            expiry=OPEN + timedelta(days=days),
            underlying="GOLDM",
        )

    near, far = future("GOLDM26JANFUT", 20), future("GOLDM26FEBFUT", 50)
    platform.instruments.add(near)
    platform.instruments.add(far)
    for inst, price in ((near, 70000.0), (far, 70450.0)):
        service.hub.publish(
            BookSnapshot(
                inst.instrument_id,
                "FYERS",
                OPEN,
                OPEN,
                (Level(price - 1, 5, 1),),
                (Level(price + 1, 5, 1),),
                last_price=price,
                open_interest=1000.0,
            )
        )
    view = service.futures_view(near.instrument_id)
    [spread] = view["calendar"]
    assert spread["near"] == near.instrument_id and spread["far"] == far.instrument_id
    assert spread["spread"] == 450.0 and spread["state"] == "CONTANGO"
    assert service.futures_view(far.instrument_id)["calendar"][0]["spread"] == 450.0
