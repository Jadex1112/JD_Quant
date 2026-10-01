"""User administration and audit (Chapters 38–40)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from jdquant.api.deps import ctx, require
from jdquant.api.schemas import AuditOut, RolesIn, StatusIn, UserCreateIn, UserOut
from jdquant.core.errors import ValidationError
from jdquant.security.identity import Principal, User, UserStatus
from jdquant.security.permissions import PRIVILEGED, ROLES

router = APIRouter(prefix="/api/v1", tags=["admin"])


def user_out(user: User) -> UserOut:
    return UserOut(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        roles=user.roles,
        status=user.status.value,
        mfa_enabled=user.mfa_enabled,
        last_login_at=user.last_login_at,
    )


@router.get("/roles")
def roles(principal: Principal = Depends(require("role:view"))) -> list[dict]:
    return [
        {"role": name, "permissions": sorted(perms), "privileged": sorted(perms & PRIVILEGED)}
        for name, perms in ROLES.items()
    ]


@router.get("/users", response_model=list[UserOut])
def list_users(request: Request, principal: Principal = Depends(require("user:view"))) -> list[UserOut]:
    return [user_out(u) for u in ctx(request).identity.list_users()]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreateIn, request: Request, principal: Principal = Depends(require("user:create"))):
    c = ctx(request)
    if body.roles:
        _assert_can_assign(request, principal)
    return user_out(
        c.identity.create_user(
            body.email, body.display_name, body.password, body.roles, actor=principal.user_id
        )
    )


def _assert_can_assign(request: Request, principal: Principal) -> None:
    require("role:assign")(request, principal)


@router.put("/users/{user_id}/roles", response_model=UserOut)
def set_roles(
    user_id: str, body: RolesIn, request: Request, principal: Principal = Depends(require("role:assign"))
):
    return user_out(ctx(request).identity.set_roles(user_id, body.roles, actor=principal.user_id))


@router.put("/users/{user_id}/status", response_model=UserOut)
def set_status(
    user_id: str, body: StatusIn, request: Request, principal: Principal = Depends(require("user:suspend"))
):
    try:
        status = UserStatus(body.status)
    except ValueError:
        raise ValidationError(
            "STATUS_INVALID", [{"field": "status", "message": f"one of {list(UserStatus)}"}]
        ) from None
    if user_id == principal.user_id and status is not UserStatus.ACTIVE:
        raise ValidationError("SELF_SUSPEND", [{"field": "user_id", "message": "cannot suspend yourself"}])
    return user_out(ctx(request).identity.set_status(user_id, status, actor=principal.user_id))


@router.get("/audit-events", response_model=list[AuditOut])
def audit_events(
    request: Request,
    actor: str | None = None,
    action: str | None = None,
    category: str | None = None,
    limit: int = 200,
    principal: Principal = Depends(require("audit:view")),
) -> list[AuditOut]:
    c = ctx(request)
    c.audit.record(
        actor=principal.user_id,
        action="audit.search",
        category="DATA_ACCESS",
        data={"actor": actor, "action": action, "category": category},
    )
    return [
        AuditOut(**r.__dict__)
        for r in c.audit.search(actor=actor, action=action, category=category, limit=min(limit, 1000))
    ]


@router.post("/audit-events:verify")
def verify_audit(request: Request, principal: Principal = Depends(require("audit:verify"))) -> dict:
    ok, broken = ctx(request).audit.verify()
    return {"valid": ok, "first_broken_seq": broken}
