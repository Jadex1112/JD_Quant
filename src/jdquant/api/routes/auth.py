"""Authentication, sessions, MFA and API keys (Chapter 40)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from jdquant.api.context import AppContext
from jdquant.api.deps import CSRF_COOKIE, SESSION_COOKIE, bearer_token, ctx, current_principal
from jdquant.api.schemas import (
    ApiKeyCreatedOut,
    ApiKeyIn,
    ApiKeyOut,
    CodeIn,
    LoginIn,
    LoginOut,
    MeOut,
    MfaEnrollOut,
    PasswordChangeIn,
    RecoveryCodesOut,
    SessionOut,
    SetupIn,
)
from jdquant.core.errors import PlatformError
from jdquant.security.identity import Principal, User
from jdquant.security.permissions import ROLES

router = APIRouter(prefix="/api/v1", tags=["auth"])


def me_out(c: AppContext, principal: Principal) -> MeOut:
    user = c.identity.get_user(principal.user_id)
    return MeOut(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        roles=user.roles,
        status=user.status.value,
        mfa_enabled=user.mfa_enabled,
        last_login_at=user.last_login_at,
        permissions=sorted(principal.permissions),
        auth_method=principal.method.value,
        mfa_recent=principal.mfa_recent,
    )


def api_key_out(key) -> ApiKeyOut:
    return ApiKeyOut(
        key_id=key.key_id,
        name=key.name,
        scopes=key.scopes,
        created_at=key.created_at,
        expires_at=key.expires_at,
        revoked_at=key.revoked_at,
        last_used_at=key.last_used_at,
    )


@router.get("/setup")
def setup_status(request: Request) -> dict:
    c = ctx(request)
    return {"setup_required": c.settings.allow_setup and not c.identity.has_users()}


@router.post("/setup", status_code=201)
def setup(body: SetupIn, request: Request) -> dict:
    """First-run bootstrap: create the owner account while no users exist (FR-40006)."""
    c = ctx(request)
    with c.store.transaction():
        if not c.settings.allow_setup or c.identity.has_users():
            raise PlatformError("SETUP_COMPLETE", "the platform already has users")
        user: User = c.identity.create_user(
            body.email, body.display_name, body.password, sorted(ROLES), actor="bootstrap"
        )
    return {"user_id": user.user_id, "email": user.email}


@router.post("/auth/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, response: Response) -> LoginOut:
    c = ctx(request)
    ip = request.client.host if request.client else ""
    token, session = c.identity.login(
        body.email, body.password, code=body.code, ip=ip, agent=request.headers.get("user-agent", "")
    )
    principal, _ = c.identity.authenticate_token(token)
    secure = c.settings.cookie_secure
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", secure=secure, path="/")
    response.set_cookie(
        CSRF_COOKIE, session.csrf_token, httponly=False, samesite="strict", secure=secure, path="/"
    )
    return LoginOut(
        token=token, csrf_token=session.csrf_token, expires_at=session.expires_at, user=me_out(c, principal)
    )


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, principal: Principal = Depends(current_principal)) -> None:
    token = bearer_token(request)
    if token:
        ctx(request).identity.logout(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/me", response_model=MeOut)
def me(request: Request, principal: Principal = Depends(current_principal)) -> MeOut:
    return me_out(ctx(request), principal)


@router.post("/me/password", status_code=204)
def change_password(
    body: PasswordChangeIn, request: Request, principal: Principal = Depends(current_principal)
):
    ctx(request).identity.change_password(principal.user_id, body.current_password, body.new_password)


@router.post("/me/mfa", response_model=MfaEnrollOut)
def begin_mfa(request: Request, principal: Principal = Depends(current_principal)) -> MfaEnrollOut:
    secret, uri = ctx(request).identity.begin_mfa_enrollment(principal.user_id)
    return MfaEnrollOut(secret=secret, provisioning_uri=uri)


@router.post("/me/mfa:confirm", response_model=RecoveryCodesOut)
def confirm_mfa(body: CodeIn, request: Request, principal: Principal = Depends(current_principal)):
    codes = ctx(request).identity.confirm_mfa_enrollment(principal.user_id, body.code)
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/auth/step-up", status_code=204)
def step_up(body: CodeIn, request: Request, principal: Principal = Depends(current_principal)) -> None:
    token = bearer_token(request)
    if token is None:
        raise PlatformError("STEP_UP_UNAVAILABLE", "step-up requires a session")
    ctx(request).identity.step_up(token, body.code)


@router.get("/me/sessions", response_model=list[SessionOut])
def sessions(request: Request, principal: Principal = Depends(current_principal)) -> list[SessionOut]:
    return [
        SessionOut(
            session_id=s.session_id,
            created_at=s.created_at,
            last_activity_at=s.last_activity_at,
            ip_address=s.ip_address,
            user_agent=s.user_agent,
            current=s.session_id == principal.session_id,
        )
        for s in ctx(request).identity.sessions_for(principal.user_id)
    ]


@router.delete("/me/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: str, request: Request, principal: Principal = Depends(current_principal)
) -> None:
    ctx(request).identity.revoke_session(principal.user_id, session_id)


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_keys(request: Request, principal: Principal = Depends(current_principal)) -> list[ApiKeyOut]:
    return [api_key_out(k) for k in ctx(request).identity.list_api_keys(principal.user_id)]


@router.post("/api-keys", response_model=ApiKeyCreatedOut, status_code=201)
def create_key(body: ApiKeyIn, request: Request, principal: Principal = Depends(current_principal)):
    key, raw = ctx(request).identity.create_api_key(principal.user_id, body.name, body.scopes, days=body.days)
    return ApiKeyCreatedOut(**api_key_out(key).model_dump(), api_key=raw)


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_key(key_id: str, request: Request, principal: Principal = Depends(current_principal)) -> None:
    ctx(request).identity.revoke_api_key(principal.user_id, key_id)
