"""Authentication and authorization dependencies (FR-39001: enforced on every request)."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Request

from jdquant.api.context import AppContext
from jdquant.core.errors import PlatformError
from jdquant.security.identity import Principal

SESSION_COOKIE = "jq_session"
CSRF_COOKIE = "jq_csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class Forbidden(PlatformError):
    pass


class Unauthenticated(PlatformError):
    pass


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


def current_principal(request: Request) -> Principal:
    c = ctx(request)
    api_key = request.headers.get("x-api-key")
    header = request.headers.get("authorization", "")
    try:
        if api_key:
            principal = c.identity.authenticate_api_key(api_key)
        elif header.lower().startswith("bearer "):
            principal, _ = c.identity.authenticate_token(header[7:].strip())
        elif request.cookies.get(SESSION_COOKIE):
            principal, session = c.identity.authenticate_token(request.cookies[SESSION_COOKIE])
            if (
                request.method not in SAFE_METHODS
                and request.headers.get("x-csrf-token") != session.csrf_token
            ):
                raise Forbidden("CSRF_FAILED", "missing or invalid CSRF token")
        else:
            raise Unauthenticated("UNAUTHENTICATED", "authentication required")
    except Forbidden:
        raise
    except PlatformError as exc:
        raise Unauthenticated(exc.code, exc.message) from None
    request.state.principal = principal
    return principal


def require(permission: str) -> Callable[..., Principal]:
    def dependency(request: Request, principal: Principal = Depends(current_principal)) -> Principal:
        c = ctx(request)
        if not principal.can(permission):
            c.audit.record(
                actor=principal.user_id,
                action=permission,
                category="AUTHORIZATION",
                outcome="DENIED",
                context={"path": request.url.path, "method": request.method},
            )
            raise Forbidden("PERMISSION_DENIED", f"missing permission {permission}")
        step_up = c.identity.requires_step_up(principal, permission)
        if step_up:
            raise Forbidden(step_up, f"{permission} requires a recent multi-factor verification")
        return principal

    return dependency


def locked(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Serialize access to in-memory trading state across request threads (endpoint needs `request`)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with kwargs["request"].app.state.ctx.platform.lock:
            return fn(*args, **kwargs)

    return wrapper
