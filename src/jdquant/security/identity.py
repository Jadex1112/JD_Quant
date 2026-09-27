"""Users, sessions, MFA and API keys (Chapter 40)."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from jdquant.core.clock import Clock
from jdquant.core.errors import NotFoundError, PlatformError, ValidationError
from jdquant.persistence.codec import decode, encode
from jdquant.persistence.store import Store
from jdquant.security import passwords, totp
from jdquant.security.audit import AuditLog
from jdquant.security.permissions import PRIVILEGED, ROLES, permissions_for
from jdquant.security.secrets import SecretBox


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


@dataclass
class User:
    user_id: str
    email: str
    display_name: str
    password_hash: str
    roles: list[str]
    created_at: datetime
    status: UserStatus = UserStatus.ACTIVE
    mfa_secret_encrypted: str | None = None
    mfa_pending_secret_encrypted: str | None = None
    recovery_code_hashes: list[str] = field(default_factory=list)
    last_totp_counter: int = 0
    failed_logins: int = 0
    lockouts: int = 0
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    timezone: str = "UTC"

    @property
    def mfa_enabled(self) -> bool:
        return self.mfa_secret_encrypted is not None


@dataclass
class Session:
    session_id: str
    user_id: str
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    ip_address: str = ""
    user_agent: str = ""
    mfa_verified_at: datetime | None = None
    csrf_token: str = ""


@dataclass
class ApiKey:
    key_id: str
    user_id: str
    name: str
    secret_hash: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None


class AuthMethod(StrEnum):
    SESSION = "SESSION"
    API_KEY = "API_KEY"


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    display_name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    method: AuthMethod
    session_id: str | None = None
    mfa_enabled: bool = False
    mfa_recent: bool = False

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


AUTH_FAILED = PlatformError("AUTHENTICATION_FAILED", "invalid email, password or code")


class IdentityService:
    def __init__(
        self,
        store: Store,
        clock: Clock,
        audit: AuditLog,
        box: SecretBox,
        *,
        idle_timeout: timedelta = timedelta(minutes=30),
        absolute_timeout: timedelta = timedelta(hours=12),
        max_failed_logins: int = 5,
        lockout: timedelta = timedelta(minutes=15),
        step_up_window: timedelta = timedelta(minutes=10),
        enforce_mfa_for_privileged: bool = True,
    ):
        self._store = store
        self._clock = clock
        self._audit = audit
        self._box = box
        self.idle_timeout = idle_timeout
        self.absolute_timeout = absolute_timeout
        self.max_failed_logins = max_failed_logins
        self.lockout = lockout
        self.step_up_window = step_up_window
        self.enforce_mfa_for_privileged = enforce_mfa_for_privileged

    # ---- users -----------------------------------------------------------------------------

    def has_users(self) -> bool:
        return bool(self._store.query("SELECT 1 FROM users LIMIT 1"))

    def create_user(
        self, email: str, display_name: str, password: str, roles: list[str], *, actor: str
    ) -> User:
        email = email.strip().lower()
        unknown = [r for r in roles if r not in ROLES]
        if unknown:
            raise ValidationError("ROLE_UNKNOWN", [{"field": "roles", "message": f"unknown roles {unknown}"}])
        if "@" not in email:
            raise ValidationError(
                "EMAIL_INVALID", [{"field": "email", "message": "must be an email address"}]
            )
        if self._store.query("SELECT 1 FROM users WHERE email = ?", (email,)):
            raise PlatformError("USER_EXISTS", f"a user with email {email} already exists")
        passwords.check_policy(password, email)
        user = User(
            user_id=str(uuid.uuid4()),
            email=email,
            display_name=display_name,
            password_hash=passwords.hash_password(password),
            roles=list(roles),
            created_at=self._clock.now(),
        )
        self._save(user, insert=True)
        self._audit.record(
            actor=actor,
            action="user.create",
            category="USER_ADMIN",
            target=user.user_id,
            data={"email": email, "roles": roles},
        )
        return user

    def get_user(self, user_id: str) -> User:
        rows = self._store.query("SELECT data FROM users WHERE user_id = ?", (user_id,))
        if not rows:
            raise NotFoundError("USER_NOT_FOUND", f"unknown user {user_id}")
        return decode(User, json.loads(rows[0]["data"]))

    def find_by_email(self, email: str) -> User | None:
        rows = self._store.query("SELECT data FROM users WHERE email = ?", (email.strip().lower(),))
        return decode(User, json.loads(rows[0]["data"])) if rows else None

    def list_users(self) -> list[User]:
        return [decode(User, json.loads(r["data"])) for r in self._store.query("SELECT data FROM users")]

    def set_roles(self, user_id: str, roles: list[str], *, actor: str) -> User:
        unknown = [r for r in roles if r not in ROLES]
        if unknown:
            raise ValidationError("ROLE_UNKNOWN", [{"field": "roles", "message": f"unknown roles {unknown}"}])
        user = self.get_user(user_id)
        before = list(user.roles)
        user.roles = list(roles)
        self._save(user)
        self._audit.record(
            actor=actor,
            action="user.roles.update",
            category="AUTHORIZATION",
            target=user_id,
            data={"before": before, "after": roles},
        )
        return user

    def set_status(self, user_id: str, status: UserStatus, *, actor: str) -> User:
        user = self.get_user(user_id)
        user.status = status
        self._save(user)
        if status is not UserStatus.ACTIVE:
            self.revoke_all(user_id)
        self._audit.record(
            actor=actor, action=f"user.status.{status.value.lower()}", category="USER_ADMIN", target=user_id
        )
        return user

    def change_password(self, user_id: str, current: str, new: str) -> None:
        user = self.get_user(user_id)
        if not passwords.verify_password(current, user.password_hash):
            raise AUTH_FAILED
        passwords.check_policy(new, user.email)
        user.password_hash = passwords.hash_password(new)
        self._save(user)
        self.revoke_all(user_id)
        self._audit.record(
            actor=user_id, action="user.password.change", category="AUTHENTICATION", target=user_id
        )

    def _save(self, user: User, *, insert: bool = False) -> None:
        data = json.dumps(encode(user))
        if insert:
            self._store.execute(
                "INSERT INTO users(user_id, email, data) VALUES (?,?,?)", (user.user_id, user.email, data)
            )
        else:
            self._store.execute("UPDATE users SET data = ? WHERE user_id = ?", (data, user.user_id))

    # ---- login ------------------------------------------------------------------------------

    def login(
        self, email: str, password: str, *, code: str | None = None, ip: str = "", agent: str = ""
    ) -> tuple[str, Session]:
        """Return (bearer token, session). Unknown user and wrong password are indistinguishable."""
        now = self._clock.now()
        user = self.find_by_email(email)
        if user is None:
            passwords.verify_password(password, passwords.DUMMY_HASH)
            self._audit.record(
                actor=email.strip().lower(),
                action="auth.login",
                category="AUTHENTICATION",
                outcome="FAILURE",
                reason="unknown user",
                context={"ip": ip},
            )
            raise AUTH_FAILED
        if user.locked_until and now < user.locked_until:
            self._audit.record(
                actor=user.user_id,
                action="auth.login",
                category="AUTHENTICATION",
                outcome="DENIED",
                reason="account locked",
                context={"ip": ip},
            )
            raise PlatformError("ACCOUNT_LOCKED", "too many failed attempts; try again later")
        if user.status is not UserStatus.ACTIVE or not passwords.verify_password(
            password, user.password_hash
        ):
            self._register_failure(user, ip)
            raise AUTH_FAILED
        mfa_verified_at = None
        if user.mfa_enabled:
            if not code:
                raise PlatformError("MFA_REQUIRED", "a one-time code is required")
            if not self._check_mfa(user, code):
                self._register_failure(user, ip)
                raise AUTH_FAILED
            mfa_verified_at = now
        user.failed_logins = 0
        user.lockouts = 0
        user.locked_until = None
        user.last_login_at = now
        self._save(user)
        token = secrets.token_urlsafe(32)
        session = Session(
            session_id=str(uuid.uuid4()),
            user_id=user.user_id,
            created_at=now,
            last_activity_at=now,
            expires_at=now + self.absolute_timeout,
            ip_address=ip,
            user_agent=agent,
            mfa_verified_at=mfa_verified_at,
            csrf_token=secrets.token_urlsafe(24),
        )
        self._store.execute(
            "INSERT INTO sessions(token_hash, user_id, data) VALUES (?,?,?)",
            (_sha256(token), user.user_id, json.dumps(encode(session))),
        )
        self._audit.record(
            actor=user.user_id,
            action="auth.login",
            category="AUTHENTICATION",
            context={"ip": ip, "mfa": mfa_verified_at is not None, "session": session.session_id},
        )
        return token, session

    def _register_failure(self, user: User, ip: str) -> None:
        user.failed_logins += 1
        if user.failed_logins >= self.max_failed_logins:
            user.locked_until = self._clock.now() + self.lockout * (2**user.lockouts)
            user.lockouts += 1
            user.failed_logins = 0
        self._save(user)
        self._audit.record(
            actor=user.user_id,
            action="auth.login",
            category="AUTHENTICATION",
            outcome="FAILURE",
            context={"ip": ip},
        )

    def logout(self, token: str) -> None:
        self._store.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha256(token),))

    def revoke_all(self, user_id: str) -> None:
        now = self._clock.now()
        with self._store.transaction():
            self._store.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            for key in self.list_api_keys(user_id):
                if key.revoked_at is None:
                    key.revoked_at = now
                    self._save_key(key)

    def sessions_for(self, user_id: str) -> list[Session]:
        return [
            decode(Session, json.loads(r["data"]))
            for r in self._store.query("SELECT data FROM sessions WHERE user_id = ?", (user_id,))
        ]

    def revoke_session(self, user_id: str, session_id: str) -> None:
        for r in self._store.query("SELECT token_hash, data FROM sessions WHERE user_id = ?", (user_id,)):
            if json.loads(r["data"])["session_id"] == session_id:
                self._store.execute("DELETE FROM sessions WHERE token_hash = ?", (r["token_hash"],))
                return
        raise NotFoundError("SESSION_NOT_FOUND", "unknown session")

    # ---- token resolution --------------------------------------------------------------------

    def authenticate_token(self, token: str) -> tuple[Principal, Session]:
        now = self._clock.now()
        token_hash = _sha256(token)
        rows = self._store.query("SELECT data FROM sessions WHERE token_hash = ?", (token_hash,))
        if not rows:
            raise PlatformError("UNAUTHENTICATED", "session is invalid or expired")
        session = decode(Session, json.loads(rows[0]["data"]))
        if now >= session.expires_at or now - session.last_activity_at > self.idle_timeout:
            self._store.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            raise PlatformError("UNAUTHENTICATED", "session is invalid or expired")
        user = self.get_user(session.user_id)
        if user.status is not UserStatus.ACTIVE:
            raise PlatformError("UNAUTHENTICATED", "user is not active")
        session.last_activity_at = now
        self._store.execute(
            "UPDATE sessions SET data = ? WHERE token_hash = ?", (json.dumps(encode(session)), token_hash)
        )
        return self._principal(user, AuthMethod.SESSION, session), session

    def authenticate_api_key(self, raw: str) -> Principal:
        try:
            key_id, secret = raw.removeprefix("jq_").split(".", 1)
        except ValueError:
            raise PlatformError("UNAUTHENTICATED", "malformed API key") from None
        rows = self._store.query("SELECT data FROM api_keys WHERE key_id = ?", (key_id,))
        if not rows:
            raise PlatformError("UNAUTHENTICATED", "invalid API key")
        key = decode(ApiKey, json.loads(rows[0]["data"]))
        now = self._clock.now()
        if (
            not hmac.compare_digest(key.secret_hash, _sha256(secret))
            or key.revoked_at
            or now >= key.expires_at
        ):
            raise PlatformError("UNAUTHENTICATED", "invalid API key")
        user = self.get_user(key.user_id)
        if user.status is not UserStatus.ACTIVE:
            raise PlatformError("UNAUTHENTICATED", "user is not active")
        key.last_used_at = now
        self._save_key(key)
        base = self._principal(user, AuthMethod.API_KEY, None)
        scoped = base.permissions & frozenset(key.scopes)  # never more than the owner holds (FR-39015)
        return Principal(
            base.user_id,
            base.email,
            base.display_name,
            base.roles,
            scoped - PRIVILEGED,
            AuthMethod.API_KEY,
            None,
            user.mfa_enabled,
            False,
        )

    def _principal(self, user: User, method: AuthMethod, session: Session | None) -> Principal:
        recent = bool(
            session
            and session.mfa_verified_at
            and self._clock.now() - session.mfa_verified_at <= self.step_up_window
        )
        return Principal(
            user.user_id,
            user.email,
            user.display_name,
            tuple(user.roles),
            permissions_for(user.roles),
            method,
            session.session_id if session else None,
            user.mfa_enabled,
            recent,
        )

    def requires_step_up(self, principal: Principal, permission: str) -> str | None:
        """Return an error code if a privileged action needs MFA first (FR-39008)."""
        if permission not in PRIVILEGED or not self.enforce_mfa_for_privileged:
            return None
        if not principal.mfa_enabled:
            return "MFA_ENROLLMENT_REQUIRED"
        if not principal.mfa_recent:
            return "STEP_UP_REQUIRED"
        return None

    # ---- MFA --------------------------------------------------------------------------------

    def begin_mfa_enrollment(self, user_id: str) -> tuple[str, str]:
        user = self.get_user(user_id)
        secret = totp.new_secret()
        user.mfa_pending_secret_encrypted = self._box.encrypt(secret).decode()
        self._save(user)
        return secret, totp.provisioning_uri(secret, user.email)

    def confirm_mfa_enrollment(self, user_id: str, code: str) -> list[str]:
        user = self.get_user(user_id)
        if user.mfa_pending_secret_encrypted is None:
            raise PlatformError("MFA_NOT_PENDING", "start enrollment first")
        secret = self._box.decrypt(user.mfa_pending_secret_encrypted.encode())
        counter = totp.matched_counter(secret, code, self._clock.now())
        if counter is None:
            raise PlatformError("MFA_CODE_INVALID", "the code is not valid")
        user.last_totp_counter = counter
        codes = [secrets.token_hex(5) for _ in range(10)]
        user.mfa_secret_encrypted = user.mfa_pending_secret_encrypted
        user.mfa_pending_secret_encrypted = None
        user.recovery_code_hashes = [_sha256(c) for c in codes]
        self._save(user)
        self._audit.record(actor=user_id, action="auth.mfa.enroll", category="AUTHENTICATION", target=user_id)
        return codes

    def step_up(self, token: str, code: str) -> None:
        principal, session = self.authenticate_token(token)
        user = self.get_user(principal.user_id)
        if not user.mfa_enabled or not self._check_mfa(user, code):
            self._audit.record(
                actor=user.user_id, action="auth.step_up", category="AUTHENTICATION", outcome="FAILURE"
            )
            raise PlatformError("MFA_CODE_INVALID", "the code is not valid")
        session.mfa_verified_at = self._clock.now()
        self._store.execute(
            "UPDATE sessions SET data = ? WHERE token_hash = ?", (json.dumps(encode(session)), _sha256(token))
        )
        self._audit.record(actor=user.user_id, action="auth.step_up", category="AUTHENTICATION")

    def _check_mfa(self, user: User, code: str) -> bool:
        assert user.mfa_secret_encrypted is not None
        secret = self._box.decrypt(user.mfa_secret_encrypted.encode())
        counter = totp.matched_counter(secret, code, self._clock.now())
        if counter is not None:
            if counter <= user.last_totp_counter:
                return False  # replay of an already used code
            user.last_totp_counter = counter
            self._save(user)
            return True
        hashed = _sha256(code.strip())
        if hashed in user.recovery_code_hashes:
            user.recovery_code_hashes.remove(hashed)
            self._save(user)
            return True
        return False

    # ---- API keys ---------------------------------------------------------------------------

    def create_api_key(
        self, user_id: str, name: str, scopes: list[str], *, days: int = 90
    ) -> tuple[ApiKey, str]:
        user = self.get_user(user_id)
        allowed = permissions_for(user.roles)
        excess = sorted(set(scopes) - allowed)
        if excess:
            raise ValidationError(
                "SCOPE_NOT_PERMITTED", [{"field": "scopes", "message": f"not held: {excess}"}]
            )
        if not 1 <= days <= 365:
            raise ValidationError(
                "EXPIRY_INVALID", [{"field": "days", "message": "must be between 1 and 365"}]
            )
        secret = secrets.token_urlsafe(32)
        now = self._clock.now()
        key = ApiKey(
            uuid.uuid4().hex[:16],
            user_id,
            name,
            _sha256(secret),
            sorted(scopes),
            now,
            now + timedelta(days=days),
        )
        self._store.execute(
            "INSERT INTO api_keys(key_id, user_id, data) VALUES (?,?,?)",
            (key.key_id, user_id, json.dumps(encode(key))),
        )
        self._audit.record(
            actor=user_id,
            action="auth.api_key.create",
            category="AUTHENTICATION",
            target=key.key_id,
            data={"scopes": key.scopes},
        )
        return key, f"jq_{key.key_id}.{secret}"

    def list_api_keys(self, user_id: str) -> list[ApiKey]:
        return [
            decode(ApiKey, json.loads(r["data"]))
            for r in self._store.query("SELECT data FROM api_keys WHERE user_id = ?", (user_id,))
        ]

    def revoke_api_key(self, user_id: str, key_id: str) -> None:
        for key in self.list_api_keys(user_id):
            if key.key_id == key_id:
                key.revoked_at = self._clock.now()
                self._save_key(key)
                self._audit.record(
                    actor=user_id, action="auth.api_key.revoke", category="AUTHENTICATION", target=key_id
                )
                return
        raise NotFoundError("API_KEY_NOT_FOUND", "unknown API key")

    def _save_key(self, key: ApiKey) -> None:
        self._store.execute(
            "UPDATE api_keys SET data = ? WHERE key_id = ?", (json.dumps(encode(key)), key.key_id)
        )
