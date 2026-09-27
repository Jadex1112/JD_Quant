"""Background synchronisation with venues: order status, prices, timeouts and strategy bars."""

from __future__ import annotations

import logging
import threading

from jdquant.connectivity.base import VenueError, VenueTimeout
from jdquant.connectivity.connections import ConnectionManager
from jdquant.marketdata.records import Quote, Trade
from jdquant.platform import PAPER_ACCOUNT_ID, Platform
from jdquant.strategy.runner import DeploymentRunner

log = logging.getLogger(__name__)


class VenuePoller:
    def __init__(
        self,
        platform: Platform,
        connections: ConnectionManager,
        runner: DeploymentRunner,
        *,
        interval: float = 2.0,
    ):
        self._platform = platform
        self._connections = connections
        self._runner = runner
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="venue-poller", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception("venue poll failed")

    def tick(self) -> None:
        p = self._platform
        with p.lock:
            p.oms.check_timeouts()
            working = [o for o in p.oms.list_orders(working_only=True)]
            wanted = {o.instrument_id for o in working}
            wanted |= {pos.instrument_id for pos in p.positions.positions()}
            for deployment in p.trading.deployments.values():
                wanted |= set(deployment.instruments)
        for connection in list(self._connections.connections.values()):
            wanted |= set(connection.watchlist)

        for connection_id, adapter in list(self._connections.adapters.items()):
            connection = self._connections.connections[connection_id]
            orders = [o for o in working if o.account_id == connection.account_id]
            try:
                if orders:
                    adapter.poll(orders)
                self._connections.mark(connection_id, True)
            except (VenueError, VenueTimeout) as exc:
                self._connections.mark(connection_id, False, str(exc))

        for instrument_id in sorted(wanted):
            source = self._connections.data_source_for(instrument_id)
            if source is None:
                continue
            try:
                quote = source.fetch_quote(p.instruments.get(instrument_id))
            except Exception as exc:  # a bad symbol must not stop the loop
                log.warning("quote fetch failed for %s: %s", instrument_id, exc)
                continue
            if quote is not None:
                self.on_quote(quote)
        self._runner.tick()

    def refresh_quote(self, instrument_id: str) -> None:
        """Fetch a price on demand when the cached one is missing or stale (e.g. before an order)."""
        p = self._platform
        if not p.market.is_stale(instrument_id):
            return
        source = self._connections.data_source_for(instrument_id)
        if source is None:
            return
        try:
            quote = source.fetch_quote(p.instruments.get(instrument_id))
        except Exception as exc:
            log.warning("quote refresh failed for %s: %s", instrument_id, exc)
            return
        if quote is not None:
            self.on_quote(quote)

    def on_quote(self, quote: Quote) -> None:
        """Apply a quote: market state, paper resting orders and quote-built strategy bars."""
        p = self._platform
        with p.lock:
            p.market.on_quote(quote)
            has_paper_orders = any(
                o.instrument_id == quote.instrument_id
                for o in p.oms.list_orders(account_id=PAPER_ACCOUNT_ID, working_only=True)
            )
            if has_paper_orders:
                p.venue.on_trade(Trade(quote.instrument_id, quote.exchange_ts, quote.mid, 0))
        self._runner.on_quote(quote)
