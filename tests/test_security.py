from datetime import timedelta

import pytest
from conftest import BTC, OWNER, login_client, make_app
from fastapi.testclient import TestClient

from jdquant.security import passwords, totp
from jdquant.security.permissions import ALL_PERMISSIONS, PRIVILEGED, ROLES

ORDER = {
    "account_id": "paper-main",
    "instrument_id": BTC,
    "side": "BUY",
    "order_type": "MARKET",
    "quantity": "0.1",
}


def _login(client, email, password, code=None):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password, "code": code})


def _as(client, token):
    other = TestClient(client.app)
    other.headers["Authorization"] = f"Bearer {token}"
    return other


def _new_user(admin, email, roles):
    body = {"email": email, "display_name": email, "password": "another-strong-pass", "roles": roles}
    assert admin.post("/api/v1/users", json=body).status_code == 201
    return _as(admin, _login(admin, email, "another-strong-pass").json()["token"])


def test_requests_require_authentication(platform):
    client = TestClient(make_app(platform))
    assert client.get("/api/v1/health").status_code == 200
    resp = client.get("/api/v1/orders")
    assert resp.status_code == 401 and resp.json()["code"] == "UNAUTHENTICATED"


def test_setup_runs_only_once(platform):
    client = login_client(platform)
    assert client.get("/api/v1/setup").json() == {"setup_required": False}
    assert client.post("/api/v1/setup", json=OWNER | {"email": "x@y.z"}).status_code == 409


def test_failed_logins_are_indistinguishable_and_lock_the_account(platform):
    """FR-40025, FR-40026."""
    client = login_client(platform)
    unknown = _login(client, "nobody@example.com", "whatever-password")
    wrong = _login(client, OWNER["email"], "wrong-password-123")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]
    for _ in range(4):
        _login(client, OWNER["email"], "wrong-password-123")
    assert _login(client, OWNER["email"], OWNER["password"]).status_code == 423
    platform.clock.set(platform.clock.now() + timedelta(minutes=16))
    assert _login(client, OWNER["email"], OWNER["password"]).status_code == 200


def test_role_permissions_are_enforced_and_denials_audited(platform):
    admin = login_client(platform)
    viewer = _new_user(admin, "viewer@example.com", ["VIEWER"])
    assert viewer.get("/api/v1/orders").status_code == 200
    denied = viewer.post("/api/v1/orders", json=ORDER)
    assert denied.status_code == 403 and denied.json()["code"] == "PERMISSION_DENIED"
    events = admin.get("/api/v1/audit-events", params={"action": "order:create"}).json()
    assert events[0]["outcome"] == "DENIED"


def test_privileged_actions_require_mfa_step_up(platform):
    """FR-39008, FR-40022, CON-086."""
    client = login_client(platform, enforce_mfa=True)
    ks = client.post(
        "/api/v1/kill-switches", json={"scope": "GLOBAL", "action": "BLOCK_NEW", "reason": "drill"}
    )
    assert ks.status_code == 201  # triggering never requires step-up (FR-19046)
    release = f"/api/v1/kill-switches/{ks.json()['kill_switch_id']}:release"
    assert client.post(release, json={"reason": "done"}).json()["code"] == "MFA_ENROLLMENT_REQUIRED"

    secret = client.post("/api/v1/me/mfa").json()["secret"]
    now = platform.clock.now()
    codes = client.post("/api/v1/me/mfa:confirm", json={"code": totp.code_at(secret, now)}).json()[
        "recovery_codes"
    ]
    assert len(codes) == 10
    assert client.post(release, json={"reason": "done"}).json()["code"] == "STEP_UP_REQUIRED"
    assert client.post("/api/v1/auth/step-up", json={"code": "000000"}).status_code == 400
    replay = client.post("/api/v1/auth/step-up", json={"code": totp.code_at(secret, now)})
    assert replay.status_code == 400  # the enrollment code cannot be replayed
    now += timedelta(seconds=30)
    platform.clock.set(now)
    assert client.post("/api/v1/auth/step-up", json={"code": totp.code_at(secret, now)}).status_code == 204
    assert client.post(release, json={"reason": "done"}).status_code == 200

    # login now needs a code; a recovery code works exactly once
    assert _login(client, OWNER["email"], OWNER["password"]).json()["code"] == "MFA_REQUIRED"
    assert _login(client, OWNER["email"], OWNER["password"], codes[0]).status_code == 200
    assert _login(client, OWNER["email"], OWNER["password"], codes[0]).status_code == 401


def test_api_keys_are_scoped_and_never_privileged(platform):
    """FR-39015, FR-40043, FR-40044."""
    client = login_client(platform)
    created = client.post(
        "/api/v1/api-keys", json={"name": "bot", "scopes": ["order:view", "killswitch:release"]}
    )
    assert created.status_code == 201
    raw = created.json()["api_key"]
    assert "api_key" not in client.get("/api/v1/api-keys").json()[0]

    bot = TestClient(client.app)
    bot.headers["X-API-Key"] = raw
    assert bot.get("/api/v1/orders").status_code == 200
    assert bot.post("/api/v1/orders", json=ORDER).status_code == 403
    assert bot.get("/api/v1/me").json()["permissions"] == ["order:view"]

    client.delete(f"/api/v1/api-keys/{created.json()['key_id']}")
    assert bot.get("/api/v1/orders").status_code == 401

    too_much = client.post("/api/v1/api-keys", json={"name": "x", "scopes": ["not:real"]})
    assert too_much.status_code == 400


def test_sessions_expire_when_idle(platform):
    """FR-40040."""
    client = login_client(platform)
    assert client.get("/api/v1/me").status_code == 200
    platform.clock.set(platform.clock.now() + timedelta(minutes=31))
    assert client.get("/api/v1/me").status_code == 401


def test_cookie_sessions_require_csrf_token(platform):
    client = TestClient(make_app(platform))
    client.post("/api/v1/setup", json=OWNER)
    login = _login(client, OWNER["email"], OWNER["password"]).json()
    assert client.get("/api/v1/me").status_code == 200  # cookie is enough for reads
    assert client.post("/api/v1/orders", json=ORDER).json()["code"] == "CSRF_FAILED"
    ok = client.post("/api/v1/orders", json=ORDER, headers={"X-CSRF-Token": login["csrf_token"]})
    assert ok.status_code == 201


def test_suspending_a_user_revokes_access(platform):
    """FR-40004."""
    admin = login_client(platform)
    trader = _new_user(admin, "trader@example.com", ["QUANT_TRADER"])
    user_id = trader.get("/api/v1/me").json()["user_id"]
    assert admin.put(f"/api/v1/users/{user_id}/status", json={"status": "SUSPENDED"}).status_code == 200
    assert trader.get("/api/v1/me").status_code == 401


def test_audit_chain_detects_tampering(platform):
    """AC-38001, CON-085."""
    client = login_client(platform)
    client.post("/api/v1/orders", json=ORDER)
    assert client.post("/api/v1/audit-events:verify").json() == {"valid": True, "first_broken_seq": None}
    store = client.app.state.ctx.store
    with pytest.raises(Exception, match="append-only"):
        store.execute("UPDATE audit SET actor = 'mallory' WHERE seq = 2")
    store.execute("DROP TRIGGER audit_append_only_u")  # simulate tampering below the application
    store.execute("UPDATE audit SET actor = 'mallory' WHERE seq = 2")
    assert client.post("/api/v1/audit-events:verify").json() == {"valid": False, "first_broken_seq": 2}


def test_secrets_are_masked_in_audit(platform):
    client = login_client(platform)
    client.post("/api/v1/me/password", json={"current_password": OWNER["password"], "new_password": "x" * 14})
    records = client.app.state.ctx.audit.search()
    assert all("correct-horse" not in str(r.data) for r in records)


def test_password_policy_and_hashing():
    with pytest.raises(Exception, match="PASSWORD_POLICY"):
        passwords.check_policy("short")
    encoded = passwords.hash_password("a sufficiently long passphrase")
    assert encoded.startswith("scrypt$") and "passphrase" not in encoded
    assert passwords.verify_password("a sufficiently long passphrase", encoded)
    assert not passwords.verify_password("wrong", encoded)


def test_role_catalog_is_consistent():
    assert set(PRIVILEGED) <= ALL_PERMISSIONS
    assert "order:create" not in ROLES["VIEWER"] and "audit:view" in ROLES["AUDITOR"]
    assert not (ROLES["QUANT_RESEARCHER"] & {"order:create", "killswitch:release"})
