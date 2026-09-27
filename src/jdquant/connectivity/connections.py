"""Venue connections: credentials, instrument sync, account binding (Chapter 45.3, FR-45003 – FR-45015)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import httpx

from jdquant.connectivity.alpaca import AlpacaAdapter
from jdquant.connectivity.base import Environment, VenueAdapter, VenueError, VenueTimeout
from jdquant.connectivity.binance import BinanceSpotAdapter
from jdquant.core.errors import NotFoundError, PlatformError, ValidationError
from jdquant.marketdata.instruments import Instrument
from jdquant.persistence.codec import decode, encode
from jdquant.persistence.store import Store
from jdquant.platform import Platform
from jdquant.security.secrets import SecretStore
from jdquant.trading.engine import AccountMode, TradingAccount

log = logging.getLogger(__name__)

ADAPTERS: dict[str, type[VenueAdapter]] = {"BINANCE": BinanceSpotAdapter, "ALPACA": AlpacaAdapter}
HttpFactory = Callable[[str], httpx.Client]


class ConnectionStatus(StrEnum):
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    AUTH_FAILED = "AUTH_FAILED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


@dataclass
class Connection:
    connection_id: str
    name: str
    venue: str
    environment: Environment
    created_by: str
    created_at: datetime
    has_credentials: bool
    account_id: str | None = None
    status: ConnectionStatus = ConnectionStatus.UNKNOWN
    last_error: str | None = None
    last_tested_at: datetime | None = None
    clock_offset_ms: float | None = None
    instrument_count: int = 0
    watchlist: list[str] = field(default_factory=list)


class ConnectionManager:
    def __init__(
        self, platform: Platform, store: Store, secrets: SecretStore, http_factory: HttpFactory | None = None
    ):
        self._platform = platform
        self._store = store
        self._secrets = secrets
        self._http_factory = http_factory
        self.connections: dict[str, Connection] = {}
        self.adapters: dict[str, VenueAdapter] = {}

    # ---- lifecycle ----------------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        venue: str,
        environment: Environment,
        actor: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_currency: str = "USD",
    ) -> Connection:
        venue = venue.upper()
        if venue not in ADAPTERS:
            raise ValidationError(
                "VENUE_UNSUPPORTED", [{"field": "venue", "message": f"one of {sorted(ADAPTERS)}"}]
            )
        if bool(api_key) != bool(api_secret):
            raise ValidationError(
                "CREDENTIALS_INCOMPLETE", [{"field": "api_secret", "message": "key and secret"}]
            )
        connection = Connection(
            connection_id=uuid.uuid4().hex[:12],
            name=name,
            venue=venue,
            environment=environment,
            created_by=actor,
            created_at=self._platform.clock.now(),
            has_credentials=bool(api_key),
        )
        adapter = self._build(connection, api_key, api_secret)
        self._verify(connection, adapter)
        instruments = self._sync_instruments(connection, adapter)
        if api_key:
            self._secrets.put(self._secret_name(connection, "key"), api_key)
            self._secrets.put(self._secret_name(connection, "secret"), api_secret or "")
            connection.account_id = f"{venue.lower()}-{connection.connection_id[:8]}"
            self._platform.trading.register_account(
                TradingAccount(connection.account_id, name, venue, AccountMode.LIVE, base_currency)
            )
        connection.instrument_count = len(instruments)
        self._attach(connection, adapter)
        self._save(connection)
        return connection

    def load_all(self) -> None:
        """Re-attach persisted connections at startup; works offline using stored instruments."""
        for doc in self._store.all("connection"):
            connection = decode(Connection, doc)
            for inst_doc in self._store.all(f"instrument:{connection.connection_id}"):
                self._platform.instruments.add(decode(Instrument, inst_doc))
            if connection.status is ConnectionStatus.DISABLED:
                self.connections[connection.connection_id] = connection
                continue
            key = secret = None
            if connection.has_credentials:
                key = self._secrets.get(self._secret_name(connection, "key"))
                secret = self._secrets.get(self._secret_name(connection, "secret"))
            self._attach(connection, self._build(connection, key, secret))

    def test(self, connection_id: str) -> Connection:
        connection = self.get(connection_id)
        adapter = self.adapters.get(connection_id)
        if adapter is None:
            raise PlatformError("CONNECTION_DISABLED", "connection is disabled")
        try:
            self._verify(connection, adapter)
        except PlatformError:
            self._save(connection)
            raise
        self._save(connection)
        return connection

    def rotate(self, connection_id: str, api_key: str, api_secret: str) -> Connection:
        """Validate the new credentials before swapping them in (FR-45014)."""
        connection = self.get(connection_id)
        if not connection.has_credentials:
            raise PlatformError("CONNECTION_PUBLIC", "a public connection has no credentials to rotate")
        candidate = self._build(connection, api_key, api_secret)
        self._verify(connection, candidate)
        self._secrets.put(self._secret_name(connection, "key"), api_key)
        self._secrets.put(self._secret_name(connection, "secret"), api_secret)
        old = self.adapters.get(connection_id)
        self._attach(connection, candidate)
        if old is not None:
            old.close()
        self._save(connection)
        return connection

    def disable(self, connection_id: str) -> Connection:
        connection = self.get(connection_id)
        connection.status = ConnectionStatus.DISABLED
        adapter = self.adapters.pop(connection_id, None)
        if adapter is not None:
            adapter.close()
        if connection.account_id:
            self._platform.router.detach(connection.account_id)
        self._save(connection)
        return connection

    def set_watchlist(self, connection_id: str, instruments: list[str]) -> Connection:
        connection = self.get(connection_id)
        for instrument_id in instruments:
            if not instrument_id.startswith(f"{connection.venue}:"):
                raise ValidationError(
                    "WATCHLIST_INVALID", [{"field": "instruments", "message": instrument_id}]
                )
            self._platform.instruments.get(instrument_id)
        connection.watchlist = list(dict.fromkeys(instruments))
        self._save(connection)
        return connection

    # ---- queries ------------------------------------------------------------------------------

    def get(self, connection_id: str) -> Connection:
        try:
            return self.connections[connection_id]
        except KeyError:
            raise NotFoundError("CONNECTION_NOT_FOUND", f"unknown connection {connection_id}") from None

    def adapter_for_account(self, account_id: str) -> VenueAdapter | None:
        for connection in self.connections.values():
            if connection.account_id == account_id:
                return self.adapters.get(connection.connection_id)
        return None

    def data_source_for(self, instrument_id: str) -> VenueAdapter | None:
        """Any active connection of the instrument's venue can serve its market data."""
        venue = instrument_id.split(":", 1)[0]
        for connection in self.connections.values():
            adapter = self.adapters.get(connection.connection_id)
            if connection.venue == venue and adapter is not None:
                return adapter
        return None

    def mark(self, connection_id: str, ok: bool, error: str | None = None) -> None:
        connection = self.connections.get(connection_id)
        if connection is None or connection.status is ConnectionStatus.DISABLED:
            return
        status = ConnectionStatus.CONNECTED if ok else ConnectionStatus.DEGRADED
        if status is not connection.status or error != connection.last_error:
            connection.status, connection.last_error = status, error
            self._save(connection)

    # ---- internals ----------------------------------------------------------------------------

    def _build(self, connection: Connection, key: str | None, secret: str | None) -> VenueAdapter:
        http = self._http_factory(connection.venue) if self._http_factory else None
        return ADAPTERS[connection.venue](
            self._platform.clock,
            api_key=key,
            api_secret=secret,
            environment=connection.environment,
            http=http,
        )

    def _verify(self, connection: Connection, adapter: VenueAdapter) -> None:
        connection.last_tested_at = self._platform.clock.now()
        try:
            result = adapter.test_connection()
        except (VenueError, VenueTimeout) as exc:
            code = getattr(exc, "code", "VENUE_UNAVAILABLE")
            connection.status = (
                ConnectionStatus.AUTH_FAILED if code == "API_KEY_INVALID" else ConnectionStatus.DEGRADED
            )
            connection.last_error = str(exc)
            raise PlatformError("CONNECTION_TEST_FAILED", f"{connection.venue}: {exc}") from None
        if result.can_withdraw:
            # Keys able to withdraw funds are refused outright (CON-082, FR-19003).
            raise PlatformError(
                "WITHDRAWAL_PERMISSION_REFUSED", "API keys with withdrawal permission are not accepted"
            )
        if connection.has_credentials and not result.can_trade:
            raise PlatformError("TRADING_NOT_PERMITTED", "the venue reports this account cannot trade")
        connection.status = ConnectionStatus.CONNECTED
        connection.last_error = None
        connection.clock_offset_ms = result.clock_offset_ms

    def _sync_instruments(self, connection: Connection, adapter: VenueAdapter) -> list[Instrument]:
        try:
            instruments = adapter.fetch_instruments()
        except (VenueError, VenueTimeout) as exc:
            raise PlatformError("INSTRUMENT_SYNC_FAILED", str(exc)) from None
        kind = f"instrument:{connection.connection_id}"
        with self._store.transaction():
            for instrument in instruments:
                self._platform.instruments.add(instrument)
                self._store.put(kind, instrument.instrument_id, encode(instrument))
        return instruments

    def _attach(self, connection: Connection, adapter: VenueAdapter) -> None:
        platform = self._platform

        def handle(report):
            with platform.lock:
                platform.oms.on_execution_report(report)

        adapter.set_report_handler(handle)
        self.connections[connection.connection_id] = connection
        self.adapters[connection.connection_id] = adapter
        if connection.account_id:
            platform.router.attach(connection.account_id, adapter)

    def _save(self, connection: Connection) -> None:
        self._store.put("connection", connection.connection_id, encode(connection))

    @staticmethod
    def _secret_name(connection: Connection, part: str) -> str:
        return f"connection/{connection.connection_id}/{part}"
