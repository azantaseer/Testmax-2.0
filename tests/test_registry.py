"""Multi-model behaviour: adapters in one registry, Responses-API fallback for tool calls, no hidden default model."""
import json
from types import SimpleNamespace as NS

import httpx
import openai
import pytest
from sqlalchemy import select

from app import ai_providers, llm, providers
from app.ai_settings import effective_ai
from app.models import Tenant
from app.models_runner import Scan
from app.models_work import Job
from tests.conftest import GOOD_PASSWORD, login, new_client, signup_org
from tests.test_runners import add_job, pair
from tests.test_scan import body, good_map, scan_url, setup_scan, add_requirement, run_to_result

TOOLS_ERROR = "Function tools with reasoning_effort are not supported for gpt-x in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'."
TOOLS = [{"name": "browser_snapshot", "description": "snap", "input_schema": {"type": "object", "properties": {}}}]
MESSAGES = [{"role": "user", "content": "go"}]


def bad_request(message=TOOLS_ERROR):
    req = httpx.Request("POST", "https://api.example/v1/chat/completions")
    return openai.BadRequestError("bad", response=httpx.Response(400, request=req), body={"message": message})


def completion(content="ok", calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls))], usage=NS(prompt_tokens=11, completion_tokens=7))


def responses_result(text="ok", calls=()):
    out = [NS(type="reasoning", summary=[])]
    if text:
        out.append(NS(type="message", content=[NS(type="output_text", text=text)]))
    out += [NS(type="function_call", call_id=c["id"], name=c["name"], arguments=json.dumps(c["arguments"])) for c in calls]
    return NS(output=out, usage=NS(input_tokens=11, output_tokens=7))


class ResponsesOnlyClient:
    """Behaves like gpt-6.1-sol: chat completions refuse tools and point to /v1/responses; the Responses API works."""

    def __init__(self, reply=None):
        self.chat_calls, self.responses_calls = [], []
        self.chat = NS(completions=NS(create=self._chat))
        self.responses = NS(create=self._responses)
        self.reply = reply or responses_result()

    def _chat(self, **kw):
        self.chat_calls.append(kw)
        if kw.get("tools"):
            raise bad_request()
        return completion()

    def _responses(self, **kw):
        self.responses_calls.append(kw)
        return self.reply


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    providers.OpenAIProvider._responses_models.clear()
    ai_providers._TOOL_CACHE.clear()
    yield
    providers.OpenAIProvider._responses_models.clear()


# ---- models that only accept tools through the Responses API ------------------------------------------------
def test_tools_fall_back_to_the_responses_api_with_reasoning_kept_on():
    client = ResponsesOnlyClient(responses_result("", [{"id": "call_1", "name": "browser_snapshot", "arguments": {"a": 1}}]))
    out = llm.chat("openai", "gpt-x", "SYS", MESSAGES, TOOLS, client=client)
    assert out.tool_calls == [{"id": "call_1", "name": "browser_snapshot", "arguments": {"a": 1}}] and (out.tokens_in, out.tokens_out) == (11, 7)
    assert len(client.chat_calls) == 1 and len(client.responses_calls) == 1
    sent = client.responses_calls[0]
    assert sent["instructions"] == "SYS" and sent["input"] == [{"role": "user", "content": "go"}] and "reasoning" not in sent  # no trade-off
    assert sent["tools"] == [{"type": "function", "name": "browser_snapshot", "description": "snap", "parameters": {"type": "object", "properties": {}}}]
    assert out.notes and "Responses API" in out.notes[0] and "gpt-x" in out.notes[0]


def test_the_adapter_remembers_the_model_and_does_not_fail_every_turn():
    client = ResponsesOnlyClient()
    llm.chat("openai", "gpt-x", "SYS", MESSAGES, TOOLS, client=client)
    again = llm.chat("openai", "gpt-x", "SYS", MESSAGES, TOOLS, client=client)
    assert len(client.chat_calls) == 1 and len(client.responses_calls) == 2  # second conversation goes straight to /v1/responses
    assert again.notes and again.text == "ok"
    other = ResponsesOnlyClient()
    other.chat = NS(completions=NS(create=lambda **kw: (other.chat_calls.append(kw), completion())[1]))
    out = llm.chat("openai", "other-model", "S", MESSAGES, TOOLS, client=other)
    assert other.responses_calls == [] and out.notes == []  # a different model is untouched


def test_tool_results_and_the_wrap_up_flow_through_the_responses_api():
    conversation = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "looking", "tool_calls": [{"id": "c1", "name": "browser_snapshot", "arguments": {}}, {"id": "c2", "name": "browser_snapshot", "arguments": {"x": 1}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "page one"},
        {"role": "tool", "tool_call_id": "c2", "content": "page two"},
        {"role": "user", "content": "wrap up"},
    ]
    assert providers.to_responses_input(conversation) == [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "looking"},
        {"type": "function_call", "call_id": "c1", "name": "browser_snapshot", "arguments": "{}"},
        {"type": "function_call", "call_id": "c2", "name": "browser_snapshot", "arguments": '{"x": 1}'},
        {"type": "function_call_output", "call_id": "c1", "output": "page one"},
        {"type": "function_call_output", "call_id": "c2", "output": "page two"},
        {"role": "user", "content": "wrap up"},
    ]
    client = ResponsesOnlyClient(responses_result('{"status":"PARTIAL"}'))
    out = llm.chat("openai", "gpt-x", "S", conversation, TOOLS, tool_choice="none", client=client)
    assert out.text == '{"status":"PARTIAL"}' and out.tool_calls == [] and client.responses_calls[0]["tool_choice"] == "none"


def test_plain_calls_and_normal_models_never_use_the_responses_api():
    client = ResponsesOnlyClient()
    llm.complete("openai", "gpt-x", "SYS", "hello", client=client)  # no tools: chat completions as always
    assert client.responses_calls == [] and client.chat_calls
    normal = NS(chat=NS(completions=NS(create=lambda **kw: completion())), responses=NS(create=lambda **kw: pytest.fail("must not be used")))
    out = llm.chat("openai", "gpt-5.5", "S", MESSAGES, TOOLS, client=normal)
    assert out.notes == []


def test_other_bad_requests_are_not_retried_and_keep_their_real_reason():
    calls = []

    def create(**kw):
        calls.append(kw)
        raise bad_request("Invalid schema for function 'browser_snapshot'")

    client = NS(chat=NS(completions=NS(create=create)), responses=NS(create=lambda **kw: pytest.fail("must not be used")))
    with pytest.raises(llm.LLMError) as info:
        llm.chat("openai", "gpt-x", "S", MESSAGES, TOOLS, client=client)
    assert len(calls) == 1 and "Invalid schema for function" in info.value.message


def test_tool_probe_treats_a_responses_only_model_as_supported_with_a_note(monkeypatch):
    client = ResponsesOnlyClient()
    monkeypatch.setattr(ai_providers, "_client", lambda p: client)
    supported, note = _real_probe()("openai", "gpt-x")
    assert supported is True and "Responses API" in note


def _real_probe():
    import importlib

    return importlib.import_module("tests.test_scan")._REAL_CHECK


def test_test_connection_reports_the_note_and_scans_are_allowed(monkeypatch):
    admin = new_client()
    signup_org(admin)
    client = ResponsesOnlyClient()
    client.models = NS(retrieve=lambda m: {})
    monkeypatch.setattr(ai_providers, "_client", lambda p: client)
    monkeypatch.setattr(ai_providers, "check_tool_calling", lambda provider, model: _real_probe()(provider, model))
    r = admin.post("/api/tenant/ai-test", json={"provider": "openai", "model": "gpt-x"}).json()
    assert r["ok"] is True and r["tool_calling"] is True and "Note: Used OpenAI's Responses API" in r["message"] and "cannot be used" not in r["message"]


def test_the_note_travels_from_the_proxy_to_the_saved_scan(monkeypatch, db):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    runner, _, _ = pair(admin)
    runner.post("/api/runner/heartbeat", json={})
    job_id = admin.post(scan_url(pid, req["id"]), json=body()).json()["id"]
    runner.post("/api/runner/claim", json={"free_slots": 1})
    note = "Used OpenAI's Responses API for tool calls because gpt-x does not accept tools on chat completions."
    monkeypatch.setattr(llm, "chat", lambda *a, **k: llm.ChatResult("hello", [], 5, 5, [note]))
    for _ in range(2):  # twice: the same note is stored once
        assert runner.post("/api/runner/llm", json={"job_id": job_id, "messages": [], "tools": []}).status_code == 200
    assert db.scalar(select(Job.payload).where(Job.id == job_id))["notes"] == [note]
    assert runner.post(f"/api/runner/jobs/{job_id}/complete", json={"result": {"status": "COMPLETE", "stop_reason": "ok", "app_map": good_map(), "history": []}}).status_code == 200
    scan_id = admin.get(f"/api/projects/{pid}/scans").json()["scans"][0]["id"]
    assert admin.get(f"/api/projects/{pid}/scans/{scan_id}").json()["notes"] == [note]


# ---- no hidden default model -------------------------------------------------------------------------------
@pytest.fixture
def no_default(monkeypatch):
    monkeypatch.setenv("DEFAULT_AI_PROVIDER", "")
    monkeypatch.setenv("DEFAULT_AI_MODEL", "")


def test_without_any_choice_there_is_no_model(no_default, db):
    assert effective_ai(None, None).source == "none" and not effective_ai(None, None).chosen
    admin = new_client()
    me = signup_org(admin).json()
    assert me["ai"] == {"provider": None, "model": None, "source": "none"}
    assert {p["id"] for p in me["providers"]} == {"openai", "anthropic"}
    settings = admin.get("/api/tenant/ai-settings").json()
    assert settings["provider"] is None and settings["effective"]["source"] == "none"


def test_ai_work_refuses_to_start_until_a_model_is_chosen_then_runs(no_default, monkeypatch):
    from tests.test_requirements import REPLY, fake_llm, run_next, upload

    admin = new_client()
    signup_org(admin)
    pid = admin.post("/api/projects", json={"name": "P"}).json()["id"]
    r = upload(admin, pid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_model_selected" and "Organization AI Model" in r.json()["error"]["message"]
    assert admin.get(f"/api/projects/{pid}/jobs").json()["jobs"] == []  # nothing queued
    admin.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "chosen-model"})
    fake_llm(monkeypatch)
    assert upload(admin, pid).status_code == 202
    assert run_next()[1] == "succeeded"


def test_scan_also_needs_a_model(no_default, monkeypatch):
    from tests.test_requirements import fake_llm

    admin, pid = setup_scan()
    runner, _, _ = pair(admin)
    runner.post("/api/runner/heartbeat", json={})
    admin.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "tmp"})
    req = add_requirement(admin, pid, monkeypatch)
    with __import__("app.db", fromlist=["get_engine"]).get_engine().begin() as conn:
        conn.exec_driver_sql("update tenants set ai_provider=null, ai_model=null")
    r = admin.post(scan_url(pid, req["id"]), json=body())
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_model_selected"


def test_a_personal_choice_unblocks_only_that_user(no_default, monkeypatch):
    from tests.test_requirements import fake_llm, upload

    owner = new_client()
    signup_org(owner)
    owner.post("/api/users", json={"name": "Una", "email": "una@acme.example", "password": GOOD_PASSWORD})
    una = new_client()
    login(una, "una@acme.example")
    pid = owner.post("/api/projects", json={"name": "P"}).json()["id"]
    fake_llm(monkeypatch)
    assert upload(una, pid).status_code == 409
    una.put("/api/me/ai-settings", json={"provider": "anthropic", "model": "claude-una"})
    assert una.get("/api/auth/me").json()["ai"]["source"] == "user"
    assert upload(una, pid).status_code == 202
    assert upload(owner, pid).status_code == 409  # the organization still has no model


def test_operator_system_default_is_optional_and_still_works(monkeypatch):
    monkeypatch.setenv("DEFAULT_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("DEFAULT_AI_MODEL", "claude-default")
    assert effective_ai(None, None).source == "system" and effective_ai(None, None).provider == "anthropic"
    monkeypatch.setenv("DEFAULT_AI_PROVIDER", "no-such-provider")
    assert effective_ai(None, None).source == "none"  # a default naming an unknown provider is ignored, not trusted


def test_a_stored_choice_for_a_provider_that_no_longer_exists_counts_as_none(no_default):
    t = Tenant(name="T", ai_provider="retired-provider", ai_model="m")
    assert effective_ai(None, t).source == "none"


# ---- the registry: adding a provider is one adapter -------------------------------------------------------
class EchoProvider(providers.Provider):
    id = "echo"
    label = "Echo AI"
    sdk = openai  # reuse the exception classes
    key_setting = "openai_api_key"  # any configured key makes it "configured"

    def make_client(self, timeout=None):
        return object()

    def list_models(self, client):
        return ["echo-1", "echo-2"]

    def retrieve(self, client, model):
        if model not in ("echo-1", "echo-2"):
            raise openai.NotFoundError("no", response=httpx.Response(404, request=httpx.Request("GET", "https://x")), body=None)

    def complete(self, client, model, system, user):
        return llm.LLMResult(f"echo:{model}:{user}", 1, 1)

    def chat(self, client, model, system, messages, tools, tool_choice=None):
        return llm.ChatResult(f"echo-chat:{model}", [], 2, 2)


def test_a_new_provider_is_one_adapter_and_needs_no_other_change(monkeypatch):
    monkeypatch.setitem(providers.REGISTRY, "echo", EchoProvider())
    assert llm.complete("echo", "echo-1", "sys", "hi").text == "echo:echo-1:hi"
    assert llm.chat("echo", "echo-2", "sys", MESSAGES, TOOLS).text == "echo-chat:echo-2"
    assert "echo" in ai_providers.PROVIDERS and ai_providers.PROVIDERS["echo"] == "Echo AI" and ai_providers.is_configured("echo")
    admin = new_client()
    signup_org(admin)
    status = {p["id"]: p["label"] for p in admin.get("/api/tenant/ai-settings").json()["providers"]}
    assert status["echo"] == "Echo AI"
    assert admin.get("/api/tenant/ai-models?provider=echo").json()["models"] == ["echo-1", "echo-2"]
    assert admin.post("/api/tenant/ai-test", json={"provider": "echo", "model": "echo-1"}).json()["ok"] is True
    assert admin.post("/api/tenant/ai-test", json={"provider": "echo", "model": "nope"}).json()["error"]["code"] == "model_not_found"
    saved = admin.put("/api/tenant/ai-settings", json={"provider": "echo", "model": "echo-2"})  # no database constraint blocks it
    assert saved.status_code == 200 and saved.json()["effective"] == {"provider": "echo", "model": "echo-2", "source": "tenant"}
    assert {p["id"] for p in admin.get("/api/auth/me").json()["providers"]} >= {"openai", "anthropic", "echo"}


def test_unknown_providers_are_still_rejected():
    admin = new_client()
    signup_org(admin)
    assert admin.put("/api/tenant/ai-settings", json={"provider": "echo", "model": "m"}).status_code == 422
    with pytest.raises(llm.LLMError, match="Unknown AI provider"):
        llm.complete("echo", "m", "s", "u")


def test_no_provider_names_remain_in_the_call_paths():
    """Guard against re-introducing 'if provider == ...' branches outside the adapters."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    for rel in ("llm.py", "ai_providers.py", "ai_settings.py", "handlers.py", "ai_api.py", "routers/requirements.py", "routers/scans.py", "routers/runners.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert 'provider == "openai"' not in text and 'provider == "anthropic"' not in text and "'openai'" not in text, rel
