"""Permission catalog and built-in roles (Chapter 39.3 – 39.4)."""

from __future__ import annotations

RESOURCE_ACTIONS: dict[str, tuple[str, ...]] = {
    "account": ("view", "create", "update", "suspend"),
    "connection": ("view", "create", "update", "rotate", "delete"),
    "deployment": ("view", "create", "start", "pause", "stop", "flatten", "approve", "retire"),
    "autopilot": ("view", "configure", "run", "arm", "disarm"),
    "order": ("view", "create", "modify", "cancel", "cancel_all"),
    "position": ("view", "transfer"),
    "killswitch": ("view", "trigger", "release"),
    "strategy": ("view", "create", "update", "publish", "promote"),
    "backtest": ("view", "run"),
    "risk.profile": ("view", "update"),
    "risk.limit": ("approve",),
    "portfolio": ("view",),
    "marketdata": ("view", "subscribe", "import", "manage_reference"),
    "model": ("view", "train", "evaluate", "promote", "rollback"),
    "feature": ("view", "create"),
    "ai.copilot": ("use", "execute_actions"),
    "report": ("view", "generate"),
    "audit": ("view", "export", "verify"),
    "user": ("view", "create", "update", "suspend"),
    "role": ("view", "assign"),
    "settings": ("view", "update"),
    "system": ("view_health", "maintenance_mode"),
}

ALL_PERMISSIONS = frozenset(f"{r}:{a}" for r, actions in RESOURCE_ACTIONS.items() for a in actions)

# Actions requiring a recent MFA verification (FR-39008, CON-086).
PRIVILEGED = frozenset(
    {
        "killswitch:release",
        "risk.limit:approve",
        "role:assign",
        "connection:create",
        "connection:rotate",
        "deployment:approve",
        "audit:export",
        "model:promote",
        "autopilot:arm",  # lets the autopilot place real orders
    }
)


def _all(resource: str) -> set[str]:
    return {f"{resource}:{a}" for a in RESOURCE_ACTIONS[resource]}


def _view(*resources: str) -> set[str]:
    return {f"{r}:view" for r in resources}


ROLES: dict[str, frozenset[str]] = {
    "SYSTEM_ADMIN": frozenset(
        _all("user")
        | _all("role")
        | _all("settings")
        | _all("system")
        | _all("connection")
        | _all("account")
        | _view("audit", "order", "position", "deployment", "killswitch", "marketdata", "strategy", "model")
        | {"audit:verify", "marketdata:manage_reference"}
    ),
    "QUANT_RESEARCHER": frozenset(
        _all("strategy") - {"strategy:promote"}
        | _all("backtest")
        | _view("marketdata", "model", "feature", "order")
        | {"marketdata:subscribe", "model:train", "model:evaluate", "feature:create", "ai.copilot:use"}
        | {"deployment:view", "deployment:create", "position:view", "portfolio:view", "report:view"}
    ),
    "QUANT_TRADER": frozenset(
        _all("deployment")
        | _all("order")
        | {"autopilot:view", "autopilot:configure", "autopilot:run", "autopilot:disarm"}
        | {"position:view", "killswitch:view", "killswitch:trigger"}
        | _view("strategy", "portfolio", "marketdata", "backtest", "account", "model", "risk.profile")
        | {"backtest:run", "ai.copilot:use", "ai.copilot:execute_actions", "report:view"}
    ),
    "PORTFOLIO_MANAGER": frozenset(
        _view("portfolio", "deployment", "order", "position", "account", "marketdata", "backtest", "strategy")
        | _view("autopilot")
        | _all("report")
        | {"ai.copilot:use"}
    ),
    "RISK_MANAGER": frozenset(
        _all("risk.profile")
        | _all("risk.limit")
        | _all("killswitch")
        | {
            "deployment:view",
            "deployment:approve",
            "autopilot:view",
            "autopilot:arm",
            "autopilot:disarm",
            "deployment:pause",
            "deployment:stop",
            "deployment:flatten",
        }
        | {
            "order:view",
            "order:cancel",
            "order:cancel_all",
            "position:view",
            "account:view",
            "account:suspend",
        }
        | _view("marketdata", "strategy", "portfolio", "model", "backtest")
        | _all("report")
        | {"ai.copilot:use"}
    ),
    "AI_ENGINEER": frozenset(
        _all("model")
        | _all("feature")
        | {"autopilot:view", "autopilot:run"}
        | _view("marketdata", "strategy", "backtest")
        | {"backtest:run"}
        | {"ai.copilot:use"}
    ),
    "DATA_ENGINEER": frozenset(_all("marketdata") | {"connection:view", "feature:view"}),
    "OPERATIONS_ENGINEER": frozenset(
        _all("system")
        | _view("deployment", "order", "position", "account", "connection", "killswitch", "marketdata")
        | {"deployment:pause"}
    ),
    "SECURITY_ADMIN": frozenset(
        {"user:view", "user:suspend", "role:view", "connection:view", "connection:rotate"}
        | _all("audit")
        | {"settings:view"}
    ),
    "COMPLIANCE_OFFICER": frozenset(
        _all("audit")
        | _view("order", "position", "deployment", "account", "killswitch", "risk.profile")
        | _all("report")
    ),
    "VIEWER": frozenset(
        _view("portfolio", "position", "order", "marketdata", "deployment", "autopilot") | {"report:view"}
    ),
    "AUDITOR": frozenset({"audit:view", "audit:export", "audit:verify", "settings:view", "role:view"}),
}

assert all(p in ALL_PERMISSIONS for perms in ROLES.values() for p in perms)


def permissions_for(roles: list[str]) -> frozenset[str]:
    result: set[str] = set()
    for role in roles:
        result |= ROLES.get(role, frozenset())
    return frozenset(result)
