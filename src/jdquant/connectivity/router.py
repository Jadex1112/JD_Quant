"""Routes each order to the execution venue of its account (FR-22001)."""

from __future__ import annotations

from decimal import Decimal

from jdquant.oms.manager import ExecutionRouter
from jdquant.oms.orders import Order


class AccountRouter:
    def __init__(self, default: ExecutionRouter):
        self._default = default
        self._by_account: dict[str, ExecutionRouter] = {}

    def attach(self, account_id: str, router: ExecutionRouter) -> None:
        self._by_account[account_id] = router

    def detach(self, account_id: str) -> None:
        self._by_account.pop(account_id, None)

    def router_for(self, order: Order) -> ExecutionRouter:
        return self._by_account.get(order.account_id, self._default)

    def submit(self, order: Order) -> None:
        self.router_for(order).submit(order)

    def cancel(self, order: Order) -> None:
        self.router_for(order).cancel(order)

    def query(self, order: Order) -> None:
        self.router_for(order).query(order)

    def can_replace(self, order: Order) -> bool:
        router = self.router_for(order)
        if not getattr(router, "supports_replace", False):
            return False
        per_order = getattr(router, "can_replace", None)
        return per_order(order) if per_order else True

    def replace(self, order: Order, quantity: Decimal, limit_price: Decimal) -> None:
        self.router_for(order).replace(order, quantity, limit_price)

    def restore_working(self, order: Order) -> None:
        restore = getattr(self.router_for(order), "restore_working", None)
        if restore is not None:
            restore(order)
