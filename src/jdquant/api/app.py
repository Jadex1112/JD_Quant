"""Public REST API v1 (Chapter 80)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from jdquant import __version__
from jdquant.api.context import AppContext, Settings, build_context
from jdquant.api.deps import Forbidden, Unauthenticated
from jdquant.api.routes import (
    admin,
    ai,
    auth,
    automation,
    autopilot,
    connections,
    desk,
    forecasts,
    intelligence,
    lab,
    market,
    research,
    strategies,
    trading,
)
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

WEB_DIST = Path(__file__).resolve().parent.parent / "web_dist"


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
        poller, autopilot = c.services["poller"], c.services["autopilot"]
        feed, monitor = c.services.get("demo_feed"), c.services.get("monitor")
        intelligence = c.services.get("intelligence")
        if c.settings.background_polling:
            poller.start()
            autopilot.start()
            if intelligence is not None:
                intelligence.start()
            if monitor is not None:
                monitor.start()
            if feed is not None:
                feed.start()
        yield
        if feed is not None:
            feed.stop()
        if intelligence is not None:
            intelligence.stop()
        if monitor is not None:
            monitor.stop()
        autopilot.stop()
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
            # TradingView's chart is embedded only as a cross-origin frame; none of its scripts run here
            "frame-src https://www.tradingview-widget.com https://s.tradingview.com; "
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

    for module in (
        auth,
        admin,
        trading,
        research,
        connections,
        ai,
        autopilot,
        market,
        lab,
        intelligence,
        automation,
        strategies,
        forecasts,
        desk,
    ):
        app.include_router(module.router)
    _mount_web(app, c.settings.web_dir or WEB_DIST)
    return app


def _mount_web(app: FastAPI, web_dir: Path) -> None:
    """Serve the single-page web UI (Chapter 70); unknown non-API paths fall back to index.html."""
    root = web_dir.resolve()
    index = root / "index.html"
    if not index.is_file():
        return

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise NotFoundError("NOT_FOUND", f"no API route /{path}")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            # Vite fingerprints everything under assets/, so those can be cached forever.
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory jdquant.api.app:app_factory`."""
    return create_app()
