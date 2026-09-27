"""Public REST API v1 (Chapter 80)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from jdquant import __version__
from jdquant.api.context import AppContext, Settings, build_context
from jdquant.api.deps import Forbidden, Unauthenticated
from jdquant.api.routes import admin, ai, auth, connections, research, trading
from jdquant.core.errors import NotFoundError, PlatformError, ValidationError
from jdquant.platform import Platform

_STATUS_BY_CODE = {
    "INVALID_STATE_TRANSITION": 409,
    "ORDER_NOT_MODIFIABLE": 409,
    "KILL_SWITCH_NOT_ACTIVE": 409,
    "USER_EXISTS": 409,
    "SETUP_COMPLETE": 409,
    "AUTHENTICATION_FAILED": 401,
    "MFA_REQUIRED": 401,
    "ACCOUNT_LOCKED": 423,
    "MFA_CODE_INVALID": 400,
    "COPILOT_UNAVAILABLE": 503,
    "COPILOT_QUOTA_EXCEEDED": 429,
    "COPILOT_ACTION_PENDING": 409,
}


def _problem(status: int, error: PlatformError, request: Request) -> JSONResponse:
    """RFC 9457 problem details extended with a stable code (Chapter 80.2)."""
    body = {
        "type": "about:blank",
        "title": error.code,
        "status": status,
        "detail": error.message,
        "code": error.code,
        "correlation_id": request.headers.get("X-Correlation-Id") or str(uuid.uuid4()),
    }
    if isinstance(error, ValidationError):
        body["errors"] = error.violations
    elif error.details is not None:
        body["details"] = error.details
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=headers)


def create_app(
    platform: Platform | None = None,
    *,
    settings: Settings | None = None,
    context: AppContext | None = None,
) -> FastAPI:
    c = context or build_context(settings, platform)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        poller = c.services["poller"]
        if c.settings.background_polling:
            poller.start()
        yield
        poller.stop()

    app = FastAPI(title="JD Quant AI", version=__version__, lifespan=lifespan)
    app.state.ctx = c

    @app.exception_handler(PlatformError)
    async def platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        if isinstance(exc, Unauthenticated):
            status = 401
        elif isinstance(exc, Forbidden):
            status = 403
        elif isinstance(exc, NotFoundError):
            status = 404
        elif isinstance(exc, ValidationError):
            status = 400
        else:
            status = _STATUS_BY_CODE.get(exc.code, 422)
        return _problem(status, exc, request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none'",
        )
        return response

    @app.get("/api/v1/health")
    def health() -> dict:
        trading_engine = c.platform.trading
        return {
            "status": "OPERATIONAL",
            "version": __version__,
            "mode": trading_engine.mode.value,
            "maintenance_mode": trading_engine.maintenance_mode,
        }

    for module in (auth, admin, trading, research, connections, ai):
        app.include_router(module.router)
    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory jdquant.api.app:app_factory`."""
    return create_app()
