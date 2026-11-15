from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.models import Tenant, User, UserSession
from app.security import LoginThrottle, hash_password, verify_password
from tests.conftest import GOOD_PASSWORD, login, make_user, new_client, signup_org


# ---- security helpers -------------------------------------------------------
def test_password_hash_is_argon2_and_verifies():
    h = hash_password("a-long-password")
    assert h.startswith("$argon2")
    assert verify_password("a-long-password", h)
    assert not verify_password("wrong-password!", h)
    assert not verify_password("anything", None)


def test_throttle_blocks_after_limit_and_resets():
    t = LoginThrottle(max_failures=3, window_seconds=60)
    for _ in range(3):
        assert not t.blocked("a@x.example")
        t.record_failure("a@x.example")
    assert t.blocked("a@x.example")
    assert not t.blocked("b@x.example")
    t.reset("a@x.example")
    assert not t.blocked("a@x.example")


# ---- create organization ----------------------------------------------------
def test_signup_organization_creates_tenant_and_super_admin(client, db):
    r = signup_org(client)
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["role"] == "super_admin"
    assert body["tenant"]["name"] == "Acme Corp"
    assert body["ai"]["source"] == "system"
    assert client.get("/api/auth/me").json()["user"]["email"] == "owner@acme.example"
    user = db.scalar(select(User).where(User.email == "owner@acme.example"))
    assert user.status == "active" and user.password_hash.startswith("$argon2")
    assert GOOD_PASSWORD not in user.password_hash


def test_session_cookie_flags(client):
    r = signup_org(client)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age" in cookie


def test_remember_me_false_gives_session_cookie(client):
    r = signup_org(client, remember_me=False)
    assert "max-age" not in r.headers["set-cookie"].lower()


def test_password_minimum_length_is_10(client):
    r = client.post(
        "/api/auth/signup-organization",
        json={"organization_name": "Short Co", "name": "S", "email": "s@short.example", "password": "123456789"},
    )
    assert r.status_code == 422
    assert "at least 10" in r.json()["error"]["message"]
    assert "password" in r.json()["error"]["fields"]


def test_invalid_email_rejected(client):
    r = signup_org(client, email="not-an-email")
    assert r.status_code == 422


def test_duplicate_email_and_org_name_rejected_case_insensitive(client):
    assert signup_org(client).status_code == 201
    other = new_client()
    assert signup_org(other, org="Other Co", email="OWNER@acme.example").status_code == 409
    r = signup_org(other, org="ACME corp", email="second@acme.example")
    assert r.status_code == 409 and r.json()["error"]["code"] == "organization_exists"


def test_email_is_stored_lowercase_and_login_is_case_insensitive(client, db):
    signup_org(client, email="Mixed.Case@Acme.Example")
    assert db.scalar(select(User.email)) == "mixed.case@acme.example"
    assert login(new_client(), "MIXED.case@acme.example").status_code == 200


# ---- sign in / out ----------------------------------------------------------
def test_login_wrong_password_and_unknown_email_give_same_error(client):
    signup_org(client)
    wrong = login(new_client(), "owner@acme.example", "wrong-password-123")
    unknown = login(new_client(), "nobody@acme.example")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_login_success_sets_session(client):
    signup_org(client)
    c2 = new_client()
    assert login(c2, "owner@acme.example").status_code == 200
    assert c2.get("/api/auth/me").status_code == 200


def test_me_requires_authentication(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "not_authenticated"


def test_logout_invalidates_the_session(client):
    signup_org(client)
    token = client.cookies.get("tm_session")
    assert client.post("/api/auth/logout").status_code == 204
    stale = new_client()
    stale.cookies.set("tm_session", token)
    assert stale.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected(client, db):
    signup_org(client)
    db.execute(update(UserSession).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_deactivated_user_session_stops_working(client, db):
    signup_org(client)
    db.execute(update(User).values(status="pending"))
    db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_throttle_blocks_even_correct_password(client):
    signup_org(client)
    c = new_client()
    for _ in range(10):
        assert login(c, "owner@acme.example", "wrong-password-123").status_code == 401
    r = login(c, "owner@acme.example")
    assert r.status_code == 429


# ---- join organization ------------------------------------------------------
def test_join_creates_pending_user_who_cannot_sign_in(client, db):
    signup_org(client)
    tenant_id = str(db.scalar(select(Tenant.id)))
    joiner = new_client()
    r = joiner.post(
        "/api/auth/signup-join",
        json={"organization_id": tenant_id, "name": "Jo Joiner", "email": "jo@x.example", "password": GOOD_PASSWORD},
    )
    assert r.status_code == 201 and r.json()["status"] == "pending"
    assert "set-cookie" not in r.headers
    user = db.scalar(select(User).where(User.email == "jo@x.example"))
    assert user.status == "pending" and user.role == "user" and str(user.tenant_id) == tenant_id
    wrong = login(joiner, "jo@x.example", "wrong-password-123")
    assert wrong.status_code == 401
    pending = login(joiner, "jo@x.example")
    assert pending.status_code == 403 and pending.json()["error"]["code"] == "pending_approval"
    assert joiner.get("/api/auth/me").status_code == 401


def test_join_unknown_organization_and_duplicate_email(client, db):
    signup_org(client)
    tenant_id = str(db.scalar(select(Tenant.id)))
    body = {"organization_id": "00000000-0000-0000-0000-000000000000", "name": "J", "email": "j@x.example", "password": GOOD_PASSWORD}
    assert client.post("/api/auth/signup-join", json=body).status_code == 404
    body["organization_id"] = tenant_id
    body["email"] = "owner@acme.example"
    assert client.post("/api/auth/signup-join", json=body).status_code == 409


def test_organization_search_rules(client):
    for i, name in enumerate(["Alpha Labs", "Alpha Works", "Beta 100%", "Gamma_Co"]):
        signup_org(new_client(), org=name, email=f"o{i}@x.example")
    assert client.get("/api/public/organizations?q=a").json()["organizations"] == []  # too short
    names = [o["name"] for o in client.get("/api/public/organizations?q=alp").json()["organizations"]]
    assert names == ["Alpha Labs", "Alpha Works"]
    assert [o["name"] for o in client.get("/api/public/organizations?q=100%25").json()["organizations"]] == ["Beta 100%"]
    assert client.get("/api/public/organizations?q=%25%25").json()["organizations"] == []  # wildcard is escaped
    # "_" is literal, not a single-character wildcard: "Gam_" must not match "Gamm..."
    assert client.get("/api/public/organizations?q=Gam_").json()["organizations"] == []
    assert [o["name"] for o in client.get("/api/public/organizations?q=ma_C").json()["organizations"]] == ["Gamma_Co"]


def test_organization_search_is_capped_at_20(client, db):
    for i in range(25):
        db.add(Tenant(name=f"Capco {i:02d}"))
    db.commit()
    assert len(client.get("/api/public/organizations?q=capco").json()["organizations"]) == 20


# ---- misc -------------------------------------------------------------------
def test_cross_origin_write_is_blocked(client):
    r = client.post(
        "/api/auth/login",
        json={"email": "a@b.example", "password": "x"},
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"


def test_health_spa_fallback_and_headers(client):
    assert client.get("/health").json() == {"status": "ok"}
    page = client.get("/some/client/route")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert page.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in page.headers["content-security-policy"]
    nf = client.get("/api/does-not-exist")
    assert nf.status_code == 404 and nf.json()["error"]["code"] == "not_found"


def test_make_user_helper_creates_active_member(db):
    t = Tenant(name="Helper Co")
    db.add(t)
    db.commit()
    u = make_user(db, t.id, "member@helper.example")
    assert u.status == "active"
