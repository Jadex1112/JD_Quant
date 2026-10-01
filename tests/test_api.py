import pytest
from conftest import BTC, login_client


@pytest.fixture
def client(platform):
    return login_client(platform)


def _order(**kw):
    body = {
        "account_id": "paper-main",
        "instrument_id": BTC,
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": "0.25",
    }
    body.update(kw)
    return body


def test_health_and_instruments(client):
    assert client.get("/api/v1/health").json()["status"] == "OPERATIONAL"
    instruments = client.get("/api/v1/instruments", params={"query": "btc"}).json()
    assert [i["instrument_id"] for i in instruments] == [BTC]
    assert instruments[0]["reference_price"] == "50000"
    assert client.get("/api/v1/instruments/NOPE:X").status_code == 404


def test_order_lifecycle_and_positions(client):
    resp = client.post("/api/v1/orders", json=_order(), headers={"Idempotency-Key": "abc"})
    assert resp.status_code == 201
    order = resp.json()
    assert order["status"] == "FILLED" and order["average_fill_price"] == "50000"
    again = client.post("/api/v1/orders", json=_order(), headers={"Idempotency-Key": "abc"}).json()
    assert again["order_id"] == order["order_id"]

    positions = client.get("/api/v1/positions").json()
    assert positions[0]["quantity"] == "0.25"
    events = client.get(f"/api/v1/orders/{order['order_id']}/events").json()
    assert events[-1]["to"] == "FILLED"


def test_float_quantities_are_rejected(client):
    """CON-022 at the API boundary."""
    assert client.post("/api/v1/orders", json=_order(quantity=0.25)).status_code == 422


def test_risk_rejection_is_problem_details(client):
    resp = client.post("/api/v1/orders", json=_order(order_type="LIMIT", limit_price="10000"))
    assert resp.status_code == 422
    body = resp.json()
    assert resp.headers["content-type"] == "application/problem+json"
    assert body["code"] == "RISK_PRICE_DEVIATION" and body["details"]["order"]["status"] == "RISK_REJECTED"


def test_cancel_and_kill_switch(client):
    order = client.post("/api/v1/orders", json=_order(order_type="LIMIT", limit_price="49000")).json()
    assert order["status"] == "OPEN"
    assert client.delete(f"/api/v1/orders/{order['order_id']}").json()["status"] == "CANCELED"
    assert client.delete(f"/api/v1/orders/{order['order_id']}").status_code == 409

    ks = client.post(
        "/api/v1/kill-switches", json={"scope": "GLOBAL", "action": "BLOCK_NEW", "reason": "drill"}
    )
    assert ks.status_code == 201
    assert client.post("/api/v1/orders", json=_order()).json()["code"] == "KILL_SWITCH_ACTIVE"
    released = client.post(
        f"/api/v1/kill-switches/{ks.json()['kill_switch_id']}:release", json={"reason": "done"}
    )
    assert released.json()["active"] is False
    assert client.post("/api/v1/orders", json=_order()).status_code == 201


def test_backtest_endpoint(client):
    templates = client.get("/api/v1/strategy-templates").json()
    assert {"ma_crossover", "rsi_mean_reversion"} <= {t["name"] for t in templates}
    body = {
        "strategy": "ma_crossover",
        "instrument_id": BTC,
        "parameters": {"fast": 5, "slow": 20, "quantity": "0.1"},
        "data": {"start": "2025-01-01T00:00:00Z", "bars": 300, "start_price": "30000", "seed": 11},
    }
    first = client.post("/api/v1/backtests", json=body)
    assert first.status_code == 201
    second = client.post("/api/v1/backtests", json=body).json()
    assert first.json()["reproducibility_hash"] == second["reproducibility_hash"]
    assert first.json()["final_equity"] == second["final_equity"]


def test_quote_validation(client):
    bad = {"instrument_id": BTC, "bid_price": "10", "bid_size": "1", "ask_price": "9", "ask_size": "1"}
    assert client.post("/api/v1/market-data/quotes", json=bad).status_code == 400


def test_modify_order_endpoint(client):
    order = client.post("/api/v1/orders", json=_order(order_type="LIMIT", limit_price="49000")).json()
    resp = client.patch(
        f"/api/v1/orders/{order['order_id']}", json={"limit_price": "48900", "quantity": "0.2"}
    )
    assert resp.status_code == 200
    assert resp.json()["limit_price"] == "48900" and resp.json()["quantity"] == "0.2"
    bad = client.patch(f"/api/v1/orders/{order['order_id']}", json={"limit_price": "48900.001"})
    assert bad.status_code == 400


def test_web_ui_served_with_spa_fallback(platform, tmp_path):
    from fastapi.testclient import TestClient

    from jdquant.api.app import create_app
    from jdquant.api.context import Settings

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app-1234.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("nope")
    web = TestClient(create_app(platform, settings=Settings(web_dir=tmp_path)))

    index = web.get("/")
    assert index.status_code == 200 and "root" in index.text
    assert index.headers["cache-control"] == "no-cache"
    assert "default-src 'self'" in index.headers["content-security-policy"]
    deep_link = web.get("/trading")
    assert deep_link.status_code == 200 and "root" in deep_link.text
    asset = web.get("/assets/app-1234.js")
    assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
    assert "root" in web.get("/..%2Fsecret.txt").text  # traversal falls back to index, never escapes
    missing_api = web.get("/api/v1/nope")
    assert missing_api.status_code == 404 and missing_api.json()["code"] == "NOT_FOUND"
    assert web.get("/api/v1/health").json()["status"] == "OPERATIONAL"


def test_security_headers_allow_only_tradingview_frames(client):
    csp = client.get("/api/v1/health").headers["Content-Security-Policy"]
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";"))
    assert directives["default-src"] == "'self'"  # no third-party scripts in the app's page
    assert directives["frame-src"].split() == [
        "https://www.tradingview-widget.com",
        "https://s.tradingview.com",
    ]
    assert directives["frame-ancestors"] == "'none'"
