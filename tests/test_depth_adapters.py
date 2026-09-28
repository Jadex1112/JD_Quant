"""REST order books, batch day statistics and option chains from the broker adapters."""

from datetime import date

import pytest
import test_brokers
import test_fyers
import test_oanda
from conftest import T0

from jdquant.connectivity.base import Environment
from jdquant.connectivity.connections import ConnectionManager
from jdquant.core.clock import SimulatedClock
from jdquant.core.errors import PlatformError
from jdquant.intelligence.options import analyze
from jdquant.persistence.store import Store
from jdquant.platform import build_paper_platform
from jdquant.security.secrets import SecretBox, SecretStore

SBIN = "NSE:SBIN-EQ"


def test_kite_depth_drops_empty_levels_and_keeps_trade_fields():
    platform, manager, _, _ = test_brokers._setup("KITE")
    adapter = manager.data_source_for(SBIN)
    book = adapter.fetch_depth(platform.instruments.get(SBIN))
    assert book.source == "KITE" and book.is_valid()
    assert [lv.price for lv in book.bids] == [800.0, 799.95]  # the zero-price slot is gone
    assert book.asks[1].orders == 2 and book.last_price == 800.25 and book.volume == 150000
    assert book.total_buy_quantity == 90000 and book.quote().bid_price == 800


@pytest.mark.parametrize("venue", ["UPSTOX", "ANGELONE", "DHAN", "KOTAKNEO"])
def test_indian_brokers_publish_a_book(venue):
    platform, manager, _, _ = test_brokers._setup(venue)
    book = manager.data_source_for(SBIN).fetch_depth(platform.instruments.get(SBIN))
    assert book.source == venue and book.bids[0].price == 800.0 and book.asks[0].price == 800.5


@pytest.mark.parametrize("venue", ["KITE", "DHAN", "KOTAKNEO"])
def test_batch_day_statistics(venue):
    platform, manager, _, _ = test_brokers._setup(venue)
    adapter = manager.data_source_for(SBIN)
    stats = adapter.fetch_snapshots(
        [platform.instruments.get(SBIN), platform.instruments.get("NSE:GOLDBEES-EQ")]
    )
    assert stats[SBIN]["last"] == pytest.approx(800.2 if venue == "KOTAKNEO" else 800.25)
    assert stats[SBIN]["prev_close"] == pytest.approx(796.0)
    assert stats[SBIN]["volume"] == 150000


def test_fyers_depth_statistics_and_option_chain():
    platform, manager, _, _ = test_fyers._setup()
    adapter = manager.data_source_for(SBIN)
    book = adapter.fetch_depth(platform.instruments.get(SBIN))
    assert book.bids[0].quantity == 300 and book.bids[0].orders == 4 and book.volume == 123456
    stats = adapter.fetch_snapshots([platform.instruments.get(SBIN)])
    assert stats[SBIN]["prev_close"] == pytest.approx(789.9) and stats[SBIN]["volume"] == 250_000
    assert "NIFTY" in adapter.option_underlyings()
    chain = adapter.fetch_option_chain("NIFTY")
    assert chain.spot == 26_240.5 and chain.expiry == date(2026, 1, 8) and len(chain.rows) == 22
    result = analyze(chain)
    assert result["atm"] == 26_250 and result["pcr_oi"] and result["atm_iv"]


def test_dhan_option_chain_uses_dhan_iv_and_greeks():
    platform, manager, _, _ = test_brokers._setup("DHAN")
    adapter = manager.data_source_for(SBIN)
    chain = adapter.fetch_option_chain("NIFTY")
    assert chain.expiry == date(2026, 1, 8) and chain.expiries == [date(2026, 1, 8), date(2026, 1, 15)]
    row = chain.row(26_250, "CE")
    assert row.iv == pytest.approx(0.125) and row.delta == 0.5 and row.oi_change == 100_000
    with pytest.raises(PlatformError, match="cover"):
        adapter.fetch_option_chain("SBIN")


def test_oanda_depth_uses_price_buckets():
    platform, manager, _, _ = test_oanda._setup()
    book = manager.data_source_for("OANDA:XAU_USD").fetch_depth(platform.instruments.get("OANDA:XAU_USD"))
    assert book.bids and book.asks and book.bids[0].quantity == 1_000_000


def test_binance_and_delta_depth():
    from cryptography.fernet import Fernet
    from fake_brokers import FakeDelta
    from fake_venues import FakeBinance

    clock = SimulatedClock(T0)
    platform = build_paper_platform(clock, store=Store(":memory:"))
    secrets = SecretStore(platform.store, SecretBox(Fernet.generate_key()))
    binance = FakeBinance(now_ms=int(T0.timestamp() * 1000))
    manager = ConnectionManager(platform, platform.store, secrets, lambda v: binance.client())
    manager.create(
        name="B",
        venue="BINANCE",
        environment=Environment.TESTNET,
        actor="t",
        api_key="k",
        api_secret="s",
        base_currency="USDT",
    )
    book = manager.data_source_for("BINANCE:BTCUSDT").fetch_depth(platform.instruments.get("BINANCE:BTCUSDT"))
    assert len(book.bids) == 3 and book.bids[0].price > book.bids[1].price and book.capacity == 20

    delta = FakeDelta(T0)
    platform = build_paper_platform(SimulatedClock(T0), store=Store(":memory:"))
    secrets = SecretStore(platform.store, SecretBox(Fernet.generate_key()))
    manager = ConnectionManager(platform, platform.store, secrets, lambda v: delta.client())
    manager.create(
        name="D",
        venue="DELTA",
        environment=Environment.TESTNET,
        actor="t",
        api_key=delta.key,
        api_secret=delta.secret,
        base_currency="USD",
    )
    iid = next(i.instrument_id for i in platform.instruments.all() if i.venue == "DELTA")
    book = manager.data_source_for(iid).fetch_depth(platform.instruments.get(iid))
    assert book.bids[0].quantity == 120 and book.asks[1].quantity == 50


def test_kotak_neo_signs_in_with_totp_and_mpin_and_skips_other_series():
    platform, manager, conn, fake = test_brokers._setup("KOTAKNEO")
    adapter = manager.data_source_for(SBIN)
    assert fake.logins >= 1 and adapter.base_url == "https://cis.kotaksecurities.com"
    assert "NSE:ODDCO-BE" not in {i.instrument_id for i in platform.instruments.all()}
    creds = adapter.feed_credentials()
    assert creds == {"url": "wss://sfeed.kotaksecurities.com/apifeed", "user": "UCC42", "auth": "edit-sid"}
    with pytest.raises(PlatformError, match="history"):
        adapter.fetch_candles(platform.instruments.get(SBIN), 60, 10)
