import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from sqlalchemy.orm import Session

from app import jobs, llm
from app.db import get_engine
from app.models_work import Job
from app.runner_jobs import SYSTEM_PROMPTS
from tests.test_runners import add_job, org, pair

TOOLS = [{"name": "browser_click", "description": "Click", "input_schema": {"type": "object", "properties": {"element": {"type": "string"}}}}]
CONVERSATION = [
    {"role": "user", "content": "Start"},
    {"role": "assistant", "content": "Looking", "tool_calls": [{"id": "c1", "name": "browser_click", "arguments": {"element": "Login"}}, {"id": "c2", "name": "browser_click", "arguments": {"element": "Cart"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "clicked login"},
    {"role": "tool", "tool_call_id": "c2", "content": "clicked cart"},
    {"role": "user", "content": "Wrap up now"},
]


# ---- converters --------------------------------------------------------------
def test_openai_conversion():
    msgs, tools = llm.to_openai("SYS", CONVERSATION, TOOLS)
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assistant = msgs[2]
    assert assistant["tool_calls"][0] == {"id": "c1", "type": "function", "function": {"name": "browser_click", "arguments": json.dumps({"element": "Login"})}}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "clicked login"} and msgs[5] == {"role": "user", "content": "Wrap up now"}
    assert tools == [{"type": "function", "function": {"name": "browser_click", "description": "Click", "parameters": TOOLS[0]["input_schema"]}}]


def test_anthropic_conversion_groups_tool_results_before_text():
    msgs, tools = llm.to_anthropic(CONVERSATION, TOOLS)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"][0] == {"type": "text", "text": "Looking"}
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "c1", "name": "browser_click", "input": {"element": "Login"}}
    last = msgs[2]["content"]
    assert [b["type"] for b in last] == ["tool_result", "tool_result", "text"]  # results first, then the plain message
    assert last[0]["tool_use_id"] == "c1" and last[2]["text"] == "Wrap up now"
    assert tools[0] == {"name": "browser_click", "description": "Click", "input_schema": TOOLS[0]["input_schema"]}


class FakeOpenAI:
    def __init__(self, tool_args='{"element": "Login"}', content=None):
        self.kwargs = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.tool_args, self.content = tool_args, content

    def _create(self, **kwargs):
        self.kwargs = kwargs
        calls = [SimpleNamespace(id="call_1", function=SimpleNamespace(name="browser_click", arguments=self.tool_args))] if self.tool_args is not None else None
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content, tool_calls=calls))], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class FakeAnthropic:
    def __init__(self):
        self.kwargs = None
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok"), SimpleNamespace(type="tool_use", id="tu_1", name="browser_click", input={"element": "Login"})],
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )

        class Ctx:
            def __enter__(self_):
                return SimpleNamespace(get_final_message=lambda: message)

            def __exit__(self_, *a):
                return False

        return Ctx()


def test_openai_chat_parses_tool_calls_and_tolerates_bad_arguments():
    fake = FakeOpenAI()
    out = llm.chat("openai", "m", "SYS", CONVERSATION, TOOLS, client=fake)
    assert out.tool_calls == [{"id": "call_1", "name": "browser_click", "arguments": {"element": "Login"}}] and (out.tokens_in, out.tokens_out) == (10, 5)
    assert fake.kwargs["tools"] and "tool_choice" not in fake.kwargs
    assert llm.chat("openai", "m", "S", CONVERSATION, TOOLS, client=FakeOpenAI(tool_args="{broken")).tool_calls[0]["arguments"] == {}
    final = llm.chat("openai", "m", "S", CONVERSATION, TOOLS, tool_choice="none", client=FakeOpenAI(tool_args=None, content='{"status":"PARTIAL"}'))
    assert final.tool_calls == [] and final.text == '{"status":"PARTIAL"}'


def test_openai_tool_choice_none_for_the_wrap_up_call():
    fake = FakeOpenAI(tool_args=None, content="{}")
    llm.chat("openai", "m", "S", CONVERSATION, TOOLS, tool_choice="none", client=fake)
    assert fake.kwargs["tool_choice"] == "none"


def test_anthropic_chat_parses_tool_use():
    fake = FakeAnthropic()
    out = llm.chat("anthropic", "claude-x", "SYS", CONVERSATION, TOOLS, tool_choice="none", client=fake)
    assert out.text == "ok" and out.tool_calls == [{"id": "tu_1", "name": "browser_click", "arguments": {"element": "Login"}}] and (out.tokens_in, out.tokens_out) == (7, 3)
    assert fake.kwargs["system"] == "SYS" and fake.kwargs["tool_choice"] == {"type": "none"} and fake.kwargs["tools"][0]["name"] == "browser_click"


def test_chat_errors_are_translated_without_leaking():
    req = httpx.Request("POST", "https://x")
    err = openai.RateLimitError("sk-LEAK", response=httpx.Response(429, request=req), body=None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(err))))
    with pytest.raises(llm.LLMError) as info:
        llm.chat("openai", "m", "S", [], [], client=client)
    assert info.value.retryable and "LEAK" not in info.value.message


# ---- the proxy endpoint ------------------------------------------------------
@pytest.fixture
def proxy_job(monkeypatch):
    monkeypatch.setitem(SYSTEM_PROMPTS, "runner_stub", lambda job: "SERVER-HELD PROMPT")
    admin = org()
    runner, rid, key = pair(admin)
    jid = add_job("Acme Corp", payload={"provider": "openai", "model": "tenant-model"})
    runner.post("/api/runner/claim", json={"free_slots": 1})
    return runner, jid, key


def fake_chat(monkeypatch, calls):
    def chat(provider, model, system, messages, tools, tool_choice=None, client=None):
        calls.append({"provider": provider, "model": model, "system": system, "messages": messages, "tool_choice": tool_choice})
        return llm.ChatResult("hello", [{"id": "x", "name": "browser_click", "arguments": {}}], 100, 40)

    monkeypatch.setattr(llm, "chat", chat)


def test_proxy_uses_the_jobs_model_and_the_servers_prompt_and_meters_tokens(proxy_job, monkeypatch):
    runner, jid, key = proxy_job
    calls = []
    fake_chat(monkeypatch, calls)
    body = {"job_id": str(jid), "messages": [{"role": "user", "content": "hi"}], "tools": TOOLS, "tool_choice": "none"}
    r1 = runner.post("/api/runner/llm", json={**body, "system": "RUNNER-SUPPLIED PROMPT", "model": "gpt-expensive", "provider": "anthropic"})
    assert r1.status_code == 200 and r1.json() == {"text": "hello", "tool_calls": [{"id": "x", "name": "browser_click", "arguments": {}}], "tokens_in": 100, "tokens_out": 40}
    c = calls[0]
    assert (c["provider"], c["model"], c["system"], c["tool_choice"]) == ("openai", "tenant-model", "SERVER-HELD PROMPT", "none")  # nothing the runner sent was used
    runner.post("/api/runner/llm", json=body)
    with Session(get_engine()) as s:
        j = s.get(Job, jid)
    assert (j.tokens_in, j.tokens_out) == (200, 80)
    assert key not in r1.text


def test_proxy_refuses_other_runners_finished_jobs_and_job_types_without_a_prompt(proxy_job, monkeypatch):
    runner, jid, _ = proxy_job
    fake_chat(monkeypatch, [])
    admin2 = org("Tenant B", "b@b.example")
    other, _, _ = pair(admin2)
    body = {"job_id": str(jid), "messages": [], "tools": []}
    assert other.post("/api/runner/llm", json=body).status_code == 409  # another organization's runner
    assert new_client_unauth().post("/api/runner/llm", json=body).status_code == 401
    runner.post(f"/api/runner/jobs/{jid}/complete", json={"result": {}})
    assert runner.post("/api/runner/llm", json=body).status_code == 409  # a finished job cannot keep using the model
    monkeypatch.delitem(SYSTEM_PROMPTS, "runner_stub")
    j2 = add_job("Acme Corp", payload={"provider": "openai", "model": "m"})
    runner.post("/api/runner/claim", json={"free_slots": 1})
    r = runner.post("/api/runner/llm", json={"job_id": str(j2), "messages": [], "tools": []})
    assert r.status_code == 400 and r.json()["error"]["code"] == "proxy_not_allowed"


def new_client_unauth():
    from tests.conftest import new_client

    return new_client()


def test_proxy_token_budget_and_size_limit(proxy_job, monkeypatch):
    runner, jid, _ = proxy_job
    fake_chat(monkeypatch, [])
    monkeypatch.setenv("JOB_MAX_TOKENS", "100")
    ok = runner.post("/api/runner/llm", json={"job_id": str(jid), "messages": [], "tools": []})
    assert ok.status_code == 200  # 140 tokens now used, over the budget of 100
    over = runner.post("/api/runner/llm", json={"job_id": str(jid), "messages": [], "tools": []})
    assert over.status_code == 429 and over.json()["error"]["code"] == "token_budget_exceeded"
    monkeypatch.delenv("JOB_MAX_TOKENS")
    huge = runner.post("/api/runner/llm", json={"job_id": str(jid), "messages": [{"role": "user", "content": "x" * (6 * 1024 * 1024 + 10)}], "tools": []})
    assert huge.status_code == 413


def test_proxy_maps_provider_errors_for_the_runner(proxy_job, monkeypatch):
    runner, jid, _ = proxy_job

    def boom(*a, **k):
        raise llm.LLMError("OpenAI is rate limiting requests.", retryable=True)

    monkeypatch.setattr(llm, "chat", boom)
    r = runner.post("/api/runner/llm", json={"job_id": str(jid), "messages": [], "tools": []})
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_retryable"
    monkeypatch.setattr(llm, "chat", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("OpenAI rejected the server's API key.")))
    r = runner.post("/api/runner/llm", json={"job_id": str(jid), "messages": [], "tools": []})
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_failed"


def test_tokens_metered_by_the_proxy_survive_the_scan_completing(proxy_job, monkeypatch):
    """Regression: finishing a job must not wipe the running token totals the proxy recorded."""
    runner, jid, _ = proxy_job
    fake_chat(monkeypatch, [])
    runner.post("/api/runner/llm", json={"job_id": str(jid), "messages": [], "tools": []})
    runner.post("/api/runner/llm", json={"job_id": str(jid), "messages": [], "tools": []})
    assert runner.post(f"/api/runner/jobs/{jid}/complete", json={"result": {}}).json()["status"] == "succeeded"
    with Session(get_engine()) as s:
        j = s.get(Job, jid)
    assert (j.tokens_in, j.tokens_out, j.status) == (200, 80, "succeeded")
