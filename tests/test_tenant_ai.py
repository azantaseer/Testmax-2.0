import httpx
import openai
import pytest
from sqlalchemy import select

from app import ai_providers
from app.ai_settings import effective_ai
from app.deps import scoped_select
from app.models import Tenant, User
from tests.conftest import login, make_user, new_client, signup_org


def two_tenants():
    a, b = new_client(), new_client()
    signup_org(a, org="Tenant A", email="a@a.example")
    signup_org(b, org="Tenant B", email="b@b.example")
    return a, b


# ---- tenant isolation (test 2, API level) -----------------------------------
def test_each_super_admin_sees_only_their_own_tenant():
    a, b = two_tenants()
    assert a.get("/api/auth/me").json()["tenant"]["name"] == "Tenant A"
    assert b.get("/api/auth/me").json()["tenant"]["name"] == "Tenant B"


def test_ai_settings_are_separate_per_tenant(db):
    a, b = two_tenants()
    assert a.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "gpt-a"}).status_code == 200
    assert b.put("/api/tenant/ai-settings", json={"provider": "anthropic", "model": "claude-b"}).status_code == 200
    assert a.get("/api/tenant/ai-settings").json()["model"] == "gpt-a"
    assert b.get("/api/tenant/ai-settings").json()["model"] == "claude-b"
    rows = {t.name: (t.ai_provider, t.ai_model) for t in db.scalars(select(Tenant))}
    assert rows == {"Tenant A": ("openai", "gpt-a"), "Tenant B": ("anthropic", "claude-b")}


def test_changing_one_tenant_never_touches_another(db):
    a, b = two_tenants()
    b.put("/api/tenant/ai-settings", json={"provider": "anthropic", "model": "claude-b"})
    a.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "gpt-a"})
    tb = db.scalar(select(Tenant).where(Tenant.name == "Tenant B"))
    assert (tb.ai_provider, tb.ai_model) == ("anthropic", "claude-b")


def test_scoped_select_filters_by_callers_tenant(db):
    a, b = two_tenants()
    ta = db.scalar(select(Tenant).where(Tenant.name == "Tenant A"))
    tb = db.scalar(select(Tenant).where(Tenant.name == "Tenant B"))
    make_user(db, ta.id, "second@a.example")
    make_user(db, tb.id, "second@b.example")
    ua = db.scalar(select(User).where(User.email == "a@a.example"))
    emails = sorted(u.email for u in db.scalars(scoped_select(User, ua)))
    assert emails == ["a@a.example", "second@a.example"]
    with pytest.raises(TypeError):
        scoped_select(Tenant, ua)


def test_unauthenticated_calls_are_rejected():
    c = new_client()
    assert c.get("/api/tenant/ai-settings").status_code == 401
    assert c.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "m"}).status_code == 401
    assert c.get("/api/tenant/ai-models?provider=openai").status_code == 401
    assert c.post("/api/tenant/ai-test", json={"provider": "openai", "model": "m"}).status_code == 401


# ---- Super Admin only -------------------------------------------------------
@pytest.mark.parametrize("role", ["site_admin", "user"])
def test_only_super_admin_can_use_ai_settings(role, db):
    owner = new_client()
    signup_org(owner)
    tenant = db.scalar(select(Tenant))
    make_user(db, tenant.id, f"{role}@acme.example", role=role)
    c = new_client()
    assert login(c, f"{role}@acme.example").status_code == 200
    assert c.get("/api/tenant/ai-settings").status_code == 403
    assert c.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "m"}).status_code == 403
    assert c.get("/api/tenant/ai-models?provider=openai").status_code == 403
    assert c.post("/api/tenant/ai-test", json={"provider": "openai", "model": "m"}).status_code == 403


# ---- settings validation ----------------------------------------------------
def test_settings_validation(client, monkeypatch):
    signup_org(client)
    assert client.put("/api/tenant/ai-settings", json={"provider": "gemini", "model": "m"}).status_code == 422
    assert client.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "  "}).status_code == 422
    assert client.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "has space"}).status_code == 422
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    r = client.put("/api/tenant/ai-settings", json={"provider": "anthropic", "model": "claude-x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "provider_not_configured"


def test_get_settings_reports_default_and_provider_status(client, monkeypatch):
    signup_org(client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    body = client.get("/api/tenant/ai-settings").json()
    assert body["provider"] is None
    assert body["effective"] == {"provider": "openai", "model": "system-default-model", "source": "system"}
    status = {p["id"]: p["configured"] for p in body["providers"]}
    assert status == {"openai": True, "anthropic": False}
    assert "test-openai-key" not in client.get("/api/tenant/ai-settings").text


def test_saved_settings_persist_across_new_sessions(client):
    signup_org(client)
    client.put("/api/tenant/ai-settings", json={"provider": "anthropic", "model": "claude-z"})
    again = new_client()
    login(again, "owner@acme.example")
    assert again.get("/api/tenant/ai-settings").json()["model"] == "claude-z"
    assert again.get("/api/auth/me").json()["ai"] == {"provider": "anthropic", "model": "claude-z", "source": "tenant"}


# ---- effective_ai precedence ------------------------------------------------
def test_effective_ai_precedence():
    t = Tenant(name="T")
    u = User(name="u", email="u@x", password_hash="x")
    assert effective_ai(u, t).source == "system"
    t.ai_provider, t.ai_model = "anthropic", "claude-t"
    assert (effective_ai(u, t).provider, effective_ai(u, t).source) == ("anthropic", "tenant")
    u.ai_provider, u.ai_model = "openai", "gpt-u"
    e = effective_ai(u, t)
    assert (e.provider, e.model, e.source) == ("openai", "gpt-u", "user")
    u.ai_model = None  # half a choice does not count
    assert effective_ai(u, t).source == "tenant"
    t.ai_model = None
    assert effective_ai(u, t).source == "system"


# ---- providers: models list and test connection (SDK mocked) ----------------
class _FakeModels:
    def __init__(self, ids, fail=None):
        self.ids, self.fail = ids, fail

    def list(self, **_):
        if self.fail:
            raise self.fail
        return [type("M", (), {"id": i}) for i in self.ids]

    def retrieve(self, model):
        if self.fail:
            raise self.fail
        return {"id": model}


class _FakeClient:
    def __init__(self, ids=(), fail=None):
        self.models = _FakeModels(list(ids), fail)


def test_list_models_filters_non_chat_openai_models(client, monkeypatch):
    signup_org(client)
    ids = ["gpt-5.5", "text-embedding-3-large", "whisper-1", "gpt-5.5-mini", "dall-e-3", "gpt-5.5"]
    monkeypatch.setattr(ai_providers, "_client", lambda p: _FakeClient(ids))
    r = client.get("/api/tenant/ai-models?provider=openai")
    assert r.json()["models"] == ["gpt-5.5", "gpt-5.5-mini"]


def test_list_models_anthropic_returns_all(client, monkeypatch):
    signup_org(client)
    monkeypatch.setattr(ai_providers, "_client", lambda p: _FakeClient(["claude-b", "claude-a"]))
    assert client.get("/api/tenant/ai-models?provider=anthropic").json()["models"] == ["claude-a", "claude-b"]


def test_test_connection_success_and_missing_key(client, monkeypatch):
    signup_org(client)
    monkeypatch.setattr(ai_providers, "_client", lambda p: _FakeClient())
    ok = client.post("/api/tenant/ai-test", json={"provider": "openai", "model": "gpt-5.5"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    monkeypatch.undo()
    monkeypatch.setenv("OPENAI_API_KEY", "")
    bad = client.post("/api/tenant/ai-test", json={"provider": "openai", "model": "gpt-5.5"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "provider_not_configured"
    assert "No API key for OpenAI" in bad.json()["error"]["message"]


def test_provider_errors_are_translated_and_never_leak_the_key(client, monkeypatch):
    signup_org(client)
    secret = "sk-SECRET-should-never-appear"
    req = httpx.Request("GET", "https://api.example/v1/models")
    err = openai.AuthenticationError(
        f"Incorrect API key provided: {secret}", response=httpx.Response(401, request=req), body=None
    )
    monkeypatch.setattr(ai_providers, "_client", lambda p: _FakeClient(fail=err))
    for call in (
        lambda: client.get("/api/tenant/ai-models?provider=openai"),
        lambda: client.post("/api/tenant/ai-test", json={"provider": "openai", "model": "m"}),
    ):
        r = call()
        assert r.status_code == 502
        assert r.json()["error"]["code"] == "provider_key_rejected"
        assert secret not in r.text


def test_unreachable_and_not_found_translation(client, monkeypatch):
    signup_org(client)
    req = httpx.Request("GET", "https://api.example/v1/models")
    monkeypatch.setattr(
        ai_providers, "_client", lambda p: _FakeClient(fail=openai.APIConnectionError(request=req))
    )
    assert client.post("/api/tenant/ai-test", json={"provider": "openai", "model": "m"}).json()["error"]["code"] == "provider_unreachable"
    nf = openai.NotFoundError("no such model", response=httpx.Response(404, request=req), body=None)
    monkeypatch.setattr(ai_providers, "_client", lambda p: _FakeClient(fail=nf))
    r = client.post("/api/tenant/ai-test", json={"provider": "openai", "model": "m"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "model_not_found"
