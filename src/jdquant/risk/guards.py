"""Additional pre-trade guards between strategies and the broker.

The risk engine already enforces order size, notional, price deviation, position limits, open orders,
order rate, daily loss, stale data and reduce-only (Chapter 27). These guards add:

- **Duplicate orders**: the same account, instrument, side and quantity from the same source again
  within a few seconds (a retry loop or a double click), unless it only reduces a position.
- **Maximum open positions** per account.
- **Maximum exposure per asset group** (stocks, gold, forex, crypto, commodities...) per account.
- **Stop-loss required** for automated orders that open a position, when switched on.
- **Expected slippage**: a market order whose depth-walked fill would cost more than the limit.
- **Corporate events**: new positions in an instrument with results, a split, a dividend... today or
  tomorrow can be blocked (or only flagged).

Every guard fails closed to "allowed" only for orders that reduce a position: exits are never blocked
by these checks.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from jdquant.core.types import Side
from jdquant.markets.sessions import asset_group
from jdquant.oms.orders import Order, OrderSource, OrderType

KIND = "risk_guards"


@dataclass
class GuardSettings:
    duplicate_window_seconds: float = 5.0
    max_open_positions: int = 0  # 0: no limit
    max_group_notional: float = 0.0  # per asset group per account, in the account's currency; 0: no limit
    require_stop_for_automated: bool = False
    max_expected_slippage_bps: float = 50.0  # 0: no limit

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradingGuards:
    def __init__(
        self,
        store,
        platform,
        *,
        expected: Callable[[str, Side, float], dict | None] | None = None,
        corporate: Callable[[str], dict | None] | None = None,
        corporate_mode: Callable[[], str] | None = None,
        fx_to_account: Callable[[str, str], Decimal] | None = None,
    ):
        self._store = store
        self._p = platform
        self._expected = expected or (lambda *a: None)
        self._corporate = corporate or (lambda instrument_id: None)
        self._corporate_mode = corporate_mode or (lambda: "warn")  # the news desk's setting
        self._fx = fx_to_account or (lambda quote, account_currency: Decimal(1))
        saved = store.get(KIND, "settings") or {}
        self.settings = GuardSettings(
            **{k: v for k, v in saved.items() if k in GuardSettings.__dataclass_fields__}
        )
        self._recent: deque = deque(maxlen=500)  # (monotonic, key)
        self._lock = threading.Lock()
        self.blocked: deque = deque(maxlen=200)  # recent blocks, for the UI
        self.warnings: deque = deque(maxlen=200)

    def configure(self, changes: dict[str, Any]) -> dict[str, Any]:
        from jdquant.core.errors import ValidationError

        values = {
            **self.settings.to_dict(),
            **{k: v for k, v in changes.items() if k in GuardSettings.__dataclass_fields__},
        }
        if any(
            values[k] < 0
            for k in (
                "duplicate_window_seconds",
                "max_open_positions",
                "max_group_notional",
                "max_expected_slippage_bps",
            )
        ):
            raise ValidationError(
                "INVALID_SETTINGS", [{"field": "limits", "message": "must not be negative"}]
            )
        self.settings = GuardSettings(**values)
        self._store.put(KIND, "settings", self.settings.to_dict())
        return self.settings.to_dict()

    # ---- the check --------------------------------------------------------------------------------------

    def __call__(self, order: Order) -> str | None:
        if order.source is OrderSource.SYSTEM or self._reduces(order):
            return None
        for check in (
            self._duplicate,
            self._open_positions,
            self._group_exposure,
            self._stop,
            self._slippage,
            self._corporate_event,
        ):
            reason = check(order)
            if reason:
                self.blocked.append(
                    {
                        "at": self._p.clock.now().isoformat(),
                        "order_id": order.order_id,
                        "instrument_id": order.instrument_id,
                        "reason": reason,
                    }
                )
                return reason
        return None

    def _reduces(self, order: Order) -> bool:
        net = self._p.positions.net_quantity(order.account_id, order.instrument_id)
        signed = order.quantity if order.side is Side.BUY else -order.quantity
        return net != 0 and (net > 0) != (signed > 0) and abs(signed) <= abs(net)

    def _duplicate(self, order: Order) -> str | None:
        window = self.settings.duplicate_window_seconds
        if not window or order.source is not OrderSource.STRATEGY:
            return None  # manual orders are de-duplicated by their idempotency keys
        key = (
            order.account_id,
            order.instrument_id,
            order.side,
            order.quantity,
            order.order_type,
            order.limit_price,
            order.deployment_id or order.submitter,
        )
        now = time.monotonic()
        with self._lock:
            duplicate = any(k == key and now - t <= window for t, k in self._recent)
            self._recent.append((now, key))
        return "DUPLICATE_ORDER" if duplicate else None

    def _open_positions(self, order: Order) -> str | None:
        limit = self.settings.max_open_positions
        if not limit or self._p.positions.net_quantity(order.account_id, order.instrument_id) != 0:
            return None
        held = {p.instrument_id for p in self._p.positions.positions(account_id=order.account_id)}
        return "MAX_OPEN_POSITIONS" if len(held) >= limit else None

    def _group_exposure(self, order: Order) -> str | None:
        limit = self.settings.max_group_notional
        if not limit:
            return None
        instrument = self._p.instruments.get(order.instrument_id)
        group = asset_group(instrument)
        account = self._p.trading.get_account(order.account_id)
        total = Decimal(0)
        for pos in self._p.positions.positions(account_id=order.account_id):
            other = self._p.instruments.get(pos.instrument_id)
            if asset_group(other) != group:
                continue
            price = self._p.market.reference_price(pos.instrument_id) or pos.average_entry_price
            total += (
                abs(pos.quantity)
                * price
                * other.contract_multiplier
                * self._fx(other.quote_asset, account.base_currency)
            )
        price = order.limit_price or self._p.market.reference_price(order.instrument_id)
        if price is None:
            return None
        total += (
            order.quantity
            * price
            * instrument.contract_multiplier
            * self._fx(instrument.quote_asset, account.base_currency)
        )
        return "MAX_GROUP_EXPOSURE" if total > Decimal(str(limit)) else None

    def _stop(self, order: Order) -> str | None:
        if not self.settings.require_stop_for_automated or order.source is not OrderSource.STRATEGY:
            return None
        return None if order.tags.get("stop") else "STOP_LOSS_REQUIRED"

    def _slippage(self, order: Order) -> str | None:
        limit = self.settings.max_expected_slippage_bps
        if not limit or order.order_type is not OrderType.MARKET:
            return None
        estimate = self._expected(order.instrument_id, order.side, float(order.quantity))
        if (
            not estimate
            or estimate.get("slippage_bps") is None
            or "book" not in str(estimate.get("source", ""))
        ):
            return None
        if estimate.get("unfilled_in_view"):
            return "EXPECTED_SLIPPAGE"  # the visible book cannot fill it
        return "EXPECTED_SLIPPAGE" if estimate["slippage_bps"] > limit else None

    def _corporate_event(self, order: Order) -> str | None:
        mode = self._corporate_mode()
        if mode == "off":
            return None
        event = self._corporate(order.instrument_id)
        if event is None:
            return None
        if mode == "block":
            return "CORPORATE_EVENT"
        self.warnings.append(
            {
                "at": self._p.clock.now().isoformat(),
                "order_id": order.order_id,
                "instrument_id": order.instrument_id,
                "event": event,
            }
        )
        return None

    def status(self) -> dict[str, Any]:
        return {
            "settings": self.settings.to_dict(),
            "recent_blocks": list(self.blocked)[-50:],
            "recent_warnings": list(self.warnings)[-50:],
        }
