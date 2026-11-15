import pytest
from sqlalchemy import select

from app import ai_providers
from app.models import Tenant, User, UserSession
from tests.conftest import GOOD_PASSWORD, login, make_user, new_client, signup_org


def org(name="Acme Corp", email="owner@acme.example"):
    c = new_client()
    assert signup_org(c, org=name, email=email).status_code == 201
    return c


def logged_in(email, password=GOOD_PASSWORD):
    c = new_client()
    r = login(c, email, password)
    assert r.status_code == 200, r.text
    return c


def create(c, email, role="user", name=None, password=GOOD_PASSWORD):
    return c.post("/api/users", json={"name": name or email.split("@")[0], "email": email, "password": password, "role": role})


def find(c, email):
    users = c.get("/api/users?page_size=100").json()["users"]
    return next((u for u in users if u["email"] == email), None)


# ---- create / list ----------------------------------------------------------
def test_super_admin_creates_user_who_can_sign_in():
    owner = org()
    r = create(owner, "Ann@Acme.example", name="Ann  Analyst")
    assert r.status_code == 201
    body = r.json()
    assert (body["email"], body["name"], body["role"], body["status"]) == ("ann@acme.example", "Ann Analyst", "user", "active")
    assert logged_in("ann@acme.example").get("/api/auth/me").json()["tenant"]["name"] == "Acme Corp"


def test_create_validation_and_duplicates():
    owner = org()
    assert owner.post("/api/users", json={"name": "A", "email": "bad", "password": GOOD_PASSWORD}).status_code == 422
    assert owner.post("/api/users", json={"name": "A", "email": "a@x.example", "password": "short"}).status_code == 422
    assert create(owner, "dup@x.example").status_code == 201
    assert create(owner, "DUP@x.example").status_code == 409
    assert create(owner, "owner@acme.example").status_code == 409


def test_list_search_filter_and_pagination():
    owner = org()
    for i in range(12):
        create(owner, f"user{i:02d}@acme.example", name=f"User {i:02d}")
    page1 = owner.get("/api/users?page_size=5").json()
    assert page1["total"] == 13 and len(page1["users"]) == 5 and page1["page"] == 1
    page3 = owner.get("/api/users?page_size=5&page=3").json()
    assert len(page3["users"]) == 3
    found = owner.get("/api/users?q=user07").json()
    assert [u["email"] for u in found["users"]] == ["user07@acme.example"]
    assert owner.get("/api/users?q=%25%25").json()["total"] == 0  # wildcard escaped
    assert owner.get("/api/users?status=pending").json()["total"] == 0


def test_normal_users_cannot_use_user_management():
    owner = org()
    create(owner, "u@acme.example")
    u = logged_in("u@acme.example")
    assert u.get("/api/users").status_code == 403
    assert create(u, "x@acme.example").status_code == 403
    me_id = owner.get("/api/users").json()["users"][0]["id"]
    assert u.patch(f"/api/users/{me_id}", json={"name": "Hacked"}).status_code == 403
    assert u.delete(f"/api/users/{me_id}").status_code == 403


def test_unauthenticated_user_management_rejected():
    c = new_client()
    assert c.get("/api/users").status_code == 401
    assert c.post("/api/users", json={}).status_code == 401


# ---- roles ------------------------------------------------------------------
def test_site_admin_can_manage_plain_users_only():
    owner = org()
    create(owner, "site@acme.example", role="site_admin")
    create(owner, "plain@acme.example")
    site = logged_in("site@acme.example")
    assert create(site, "new@acme.example").status_code == 201
    assert create(site, "newadmin@acme.example", role="site_admin").status_code == 403
    plain = find(site, "plain@acme.example")
    assert site.patch(f"/api/users/{plain['id']}", json={"status": "disabled"}).status_code == 200
    assert site.patch(f"/api/users/{plain['id']}", json={"role": "site_admin"}).status_code == 403
    owner_row = find(site, "owner@acme.example")
    other_site = find(site, "site@acme.example")  # the site admin's own row
    assert site.delete(f"/api/users/{owner_row['id']}").status_code == 403
    assert site.delete(f"/api/users/{other_site['id']}").json()["error"]["code"] == "cannot_delete_self"
    for target in (owner_row, other_site):
        assert site.patch(f"/api/users/{target['id']}", json={"status": "disabled"}).status_code == 403
        assert site.post(f"/api/users/{target['id']}/reset-password", json={"password": GOOD_PASSWORD + "x"}).status_code == 403


def test_super_admin_changes_roles():
    owner = org()
    u = create(owner, "u@acme.example").json()
    r = owner.patch(f"/api/users/{u['id']}", json={"role": "site_admin"})
    assert r.status_code == 200 and r.json()["role"] == "site_admin"
    assert logged_in("u@acme.example").get("/api/users").status_code == 200


def test_last_super_admin_is_protected(db):
    owner = org()
    me = find(owner, "owner@acme.example")
    assert owner.patch(f"/api/users/{me['id']}", json={"role": "user"}).json()["error"]["code"] == "last_super_admin"
    assert owner.patch(f"/api/users/{me['id']}", json={"status": "disabled"}).status_code == 400
    assert owner.delete(f"/api/users/{me['id']}").json()["error"]["code"] == "cannot_delete_self"
    # with a second super admin, demoting one is fine; the remaining one is then protected
    create(owner, "two@acme.example", role="super_admin")
    assert owner.patch(f"/api/users/{me['id']}", json={"role": "user"}).status_code == 200
    two = logged_in("two@acme.example")
    two_row = find(two, "two@acme.example")
    assert two.patch(f"/api/users/{two_row['id']}", json={"role": "user"}).json()["error"]["code"] == "last_super_admin"


# ---- approve / reject / disable --------------------------------------------
def join(c_owner, db, email="jo@x.example"):
    tenant = db.scalar(select(Tenant))
    j = new_client()
    r = j.post("/api/auth/signup-join", json={"organization_id": str(tenant.id), "name": "Jo", "email": email, "password": GOOD_PASSWORD})
    assert r.status_code == 201
    return j


def test_pending_request_is_approved_then_can_sign_in(db):
    owner = org()
    j = join(owner, db)
    listing = owner.get("/api/users").json()
    assert listing["pending_total"] == 1 and listing["users"][0]["status"] == "pending"  # pending sorted first
    row = find(owner, "jo@x.example")
    assert login(j, "jo@x.example").status_code == 403
    assert owner.patch(f"/api/users/{row['id']}", json={"status": "active"}).json()["status"] == "active"
    assert login(j, "jo@x.example").status_code == 200


def test_pending_request_is_rejected_by_deleting_it(db):
    owner = org()
    join(owner, db)
    row = find(owner, "jo@x.example")
    assert owner.delete(f"/api/users/{row['id']}").status_code == 204
    assert find(owner, "jo@x.example") is None
    assert login(new_client(), "jo@x.example").status_code == 401


def test_disabling_ends_sessions_and_blocks_login_then_reenable(db):
    owner = org()
    create(owner, "u@acme.example")
    u = logged_in("u@acme.example")
    row = find(owner, "u@acme.example")
    assert owner.patch(f"/api/users/{row['id']}", json={"status": "disabled"}).status_code == 200
    assert u.get("/api/auth/me").status_code == 401
    r = login(new_client(), "u@acme.example")
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_disabled"
    assert db.scalar(select(UserSession.token_hash).where(UserSession.user_id == row["id"])) is None
    assert owner.patch(f"/api/users/{row['id']}", json={"status": "active"}).status_code == 200
    assert login(new_client(), "u@acme.example").status_code == 200


def test_cannot_disable_self():
    owner = org()
    create(owner, "two@acme.example", role="super_admin")
    me = find(owner, "owner@acme.example")
    assert owner.patch(f"/api/users/{me['id']}", json={"status": "disabled"}).json()["error"]["code"] == "cannot_disable_self"


def test_admin_resets_password_and_old_sessions_end():
    owner = org()
    create(owner, "u@acme.example")
    u = logged_in("u@acme.example")
    row = find(owner, "u@acme.example")
    assert owner.post(f"/api/users/{row['id']}/reset-password", json={"password": "brand-new-password"}).status_code == 204
    assert u.get("/api/auth/me").status_code == 401
    assert login(new_client(), "u@acme.example", GOOD_PASSWORD).status_code == 401
    assert login(new_client(), "u@acme.example", "brand-new-password").status_code == 200
    assert owner.post(f"/api/users/{row['id']}/reset-password", json={"password": "short"}).status_code == 422


def test_rename_user():
    owner = org()
    u = create(owner, "u@acme.example").json()
    assert owner.patch(f"/api/users/{u['id']}", json={"name": "  New   Name "}).json()["name"] == "New Name"
    assert owner.patch(f"/api/users/{u['id']}", json={"name": " "}).status_code == 422


# ---- tenant isolation -------------------------------------------------------
def test_admin_cannot_see_or_touch_another_tenants_users():
    a = org("Tenant A", "a@a.example")
    b = org("Tenant B", "b@b.example")
    create(b, "victim@b.example")
    victim = find(b, "victim@b.example")
    assert find(a, "victim@b.example") is None and a.get("/api/users?q=victim").json()["total"] == 0
    vid = victim["id"]
    assert a.patch(f"/api/users/{vid}", json={"status": "disabled"}).status_code == 404
    assert a.delete(f"/api/users/{vid}").status_code == 404
    assert a.post(f"/api/users/{vid}/reset-password", json={"password": "attacker-password"}).status_code == 404
    assert find(b, "victim@b.example")["status"] == "active"
    assert login(new_client(), "victim@b.example").status_code == 200


def test_admin_cannot_create_user_in_another_tenant():
    a = org("Tenant A", "a@a.example")
    org("Tenant B", "b@b.example")
    create(a, "new@a.example")
    # tenant is never taken from the request: the user lands in the caller's tenant
    assert logged_in("new@a.example").get("/api/auth/me").json()["tenant"]["name"] == "Tenant A"


# ---- per-user AI model (user setting beats tenant setting) ------------------
def test_user_override_beats_tenant_and_can_be_cleared():
    owner = org()
    owner.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "tenant-model"})
    create(owner, "u@acme.example")
    u = logged_in("u@acme.example")
    s = u.get("/api/me/ai-settings").json()
    assert s["provider"] is None and s["effective"] == {"provider": "openai", "model": "tenant-model", "source": "tenant"}
    r = u.put("/api/me/ai-settings", json={"provider": "anthropic", "model": "claude-user"})
    assert r.status_code == 200 and r.json()["effective"] == {"provider": "anthropic", "model": "claude-user", "source": "user"}
    assert u.get("/api/auth/me").json()["ai"]["source"] == "user"
    # others in the tenant are unaffected; admin list shows the user's effective model
    assert owner.get("/api/auth/me").json()["ai"]["model"] == "tenant-model"
    row = find(owner, "u@acme.example")
    assert row["ai"] == {"provider": "anthropic", "model": "claude-user", "source": "user"}
    cleared = u.delete("/api/me/ai-settings").json()
    assert cleared["provider"] is None and cleared["effective"]["source"] == "tenant"


def test_changing_tenant_model_keeps_user_override():
    owner = org()
    create(owner, "u@acme.example")
    u = logged_in("u@acme.example")
    u.put("/api/me/ai-settings", json={"provider": "openai", "model": "mine"})
    owner.put("/api/tenant/ai-settings", json={"provider": "anthropic", "model": "tenant-new"})
    assert u.get("/api/auth/me").json()["ai"] == {"provider": "openai", "model": "mine", "source": "user"}


def test_user_ai_setting_validation_and_isolation(monkeypatch):
    a = org("Tenant A", "a@a.example")
    b = org("Tenant B", "b@b.example")
    assert a.put("/api/me/ai-settings", json={"provider": "gemini", "model": "m"}).status_code == 422
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    r = a.put("/api/me/ai-settings", json={"provider": "anthropic", "model": "m"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "provider_not_configured"
    monkeypatch.undo()
    a.put("/api/me/ai-settings", json={"provider": "openai", "model": "a-model"})
    assert b.get("/api/me/ai-settings").json()["provider"] is None
    assert new_client().get("/api/me/ai-settings").status_code == 401


def test_normal_user_can_list_models_and_test_connection(monkeypatch):
    owner = org()
    create(owner, "u@acme.example")
    u = logged_in("u@acme.example")

    class M:
        def list(self, **_):
            return [type("X", (), {"id": "claude-b"}), type("X", (), {"id": "claude-a"})]

        def retrieve(self, model):
            return {}

    class C:
        models = M()

    monkeypatch.setattr(ai_providers, "_client", lambda p: C())
    assert u.get("/api/me/ai-models?provider=anthropic").json()["models"] == ["claude-a", "claude-b"]
    assert u.post("/api/me/ai-test", json={"provider": "anthropic", "model": "claude-a"}).json()["ok"] is True
    # tenant-level endpoints stay Super Admin only
    assert u.get("/api/tenant/ai-settings").status_code == 403


# ---- own password -----------------------------------------------------------
def test_change_own_password():
    owner = org()
    create(owner, "u@acme.example")
    u1, u2 = logged_in("u@acme.example"), logged_in("u@acme.example")
    assert u1.post("/api/me/password", json={"current_password": "wrong-password-x", "new_password": "another-long-password"}).status_code == 400
    assert u1.post("/api/me/password", json={"current_password": GOOD_PASSWORD, "new_password": "short"}).status_code == 422
    assert u1.post("/api/me/password", json={"current_password": GOOD_PASSWORD, "new_password": "another-long-password"}).status_code == 204
    assert u1.get("/api/auth/me").status_code == 200  # this session stays
    assert u2.get("/api/auth/me").status_code == 401  # other sessions end
    assert login(new_client(), "u@acme.example", GOOD_PASSWORD).status_code == 401
    assert login(new_client(), "u@acme.example", "another-long-password").status_code == 200
