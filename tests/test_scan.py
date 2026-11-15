import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.engines import scan_core as sc
from app.models_runner import Scan
from app.models_work import Job
from runner import scan_loop
from runner.api import ApiError
from tests.conftest import GOOD_PASSWORD, login, new_client, signup_org
from tests.test_runners import pair

SCAN_PROMPT_SHA256 = "0ebceb1c8941eb7c63d52af46601be53ff751c6709515fdfbeebf3cc7846f968"
PILOT1_PROMPT = Path(r"D:\Projects\TestMax-Independent-Pilot\system-prompts\app-scan-v1.5.txt")
URL = "https://www.saucedemo.com/"


# ---- prompt and ported helpers ----------------------------------------------
def test_scan_prompt_is_unchanged_from_pilot1():
    text, sha = sc.load_system_prompt()
    assert sha == SCAN_PROMPT_SHA256 and hashlib.sha256(text.encode()).hexdigest() == sha
    if PILOT1_PROMPT.exists():
        assert hashlib.sha256(PILOT1_PROMPT.read_bytes()).hexdigest() == sha


def test_tool_policy_matches_pilot1():
    ro = sc.allowed_tool_names(sc.ScanPolicy.READ_ONLY)
    inter = sc.allowed_tool_names(sc.ScanPolicy.INTERACTIVE)
    assert "browser_click" in ro and "browser_close" in ro and "browser_type" not in ro
    assert sc.EXECUTION_TOOLS <= inter and "browser_evaluate" not in inter and "browser_file_upload" not in inter
    origin = sc.get_authorized_origin(URL)
    assert sc.is_tool_call_permitted("browser_navigate", {"url": "https://evil.example/x"}, sc.ScanPolicy.INTERACTIVE, origin)[0] is False
    assert sc.is_tool_call_permitted("browser_navigate", {"url": "https://www.saucedemo.com/cart.html"}, sc.ScanPolicy.INTERACTIVE, origin)[0] is True
    assert sc.is_tool_call_permitted("browser_type", {}, sc.ScanPolicy.READ_ONLY, origin)[0] is False
    assert sc.is_tool_call_permitted("browser_click", {"element": "Add to cart"}, sc.ScanPolicy.READ_ONLY, origin)[0] is False
    assert sc.is_tool_call_permitted("browser_click", {"element": "Add to cart"}, sc.ScanPolicy.INTERACTIVE, origin)[0] is True


def test_secrets_are_extracted_and_scrubbed_everywhere():
    secrets = sc.extract_known_secrets("Username: standard_user, Password: secret_sauce", "pin: 4821")
    assert secrets == {"secret_sauce", "4821"}
    assert sc.scrub_secrets({"a": ["x secret_sauce y"], "b": {"c": "4821"}}, secrets) == {"a": ["x [REDACTED] y"], "b": {"c": "[REDACTED]"}}
    red = sc.redact_credentials_for_log("browser_type", {"element": "Password input", "text": "secret_sauce"})
    assert red["text"] == "[REDACTED]"
    form = sc.redact_credentials_for_log("browser_fill_form", {"fields": [{"name": "Username", "value": "bob"}, {"name": "Password", "value": "pw123"}]})
    assert form["fields"][0]["value"] == "bob" and form["fields"][1]["value"] == "[REDACTED]"


def test_app_map_validation_and_status_downgrade():
    good = {"requirementId": "r", "targetUrl": URL, "mode": "INTERACTIVE", "status": "COMPLETE", "stopReason": "", "goalReached": True,
            "pages": [{"url": URL, "name": "Login", "controls": [{"elementId": "e1", "humanName": "Login", "technical": {"locatorCandidates": [{"locator": "#login", "verification": "VERIFIED", "uniquenessChecked": False}]}}]}],
            "meaningfulActions": [{"sequence": 1, "evidenceStatus": "DISCOVERED"}], "observedBehaviors": [], "conflicts": [], "gaps": [], "evidenceRefs": []}
    issues = sc.validate_app_map_schema(good)
    assert any("downgraded VERIFIED" in i for i in issues) and good["pages"][0]["controls"][0]["technical"]["locatorCandidates"][0]["verification"] == "UNVERIFIED"
    assert sc.derive_validated_status(good, "COMPLETE")[0] == "PARTIAL"  # no OUTCOME_OBSERVED action
    good["meaningfulActions"][0]["evidenceStatus"] = "OUTCOME_OBSERVED"
    assert sc.derive_validated_status(good, "COMPLETE")[0] == "COMPLETE"
    assert sc.validate_app_map_schema([]) == ["App Map is not a JSON object."]


# ---- the runner's scan loop (fake browser tools, fake model) ---------------
def good_map(status="COMPLETE", outcome=True):
    return {
        "requirementId": "REQ-001", "targetUrl": URL, "mode": "INTERACTIVE", "status": status, "stopReason": "done", "goalReached": True,
        "pages": [{"url": URL, "name": "Login", "controls": [
            {"elementId": "login-btn", "type": "button", "humanName": "Login", "technical": {"locatorCandidates": [{"locator": "getByRole('button', {name: 'Login'})", "verification": "VERIFIED", "uniquenessChecked": True}]}},
            {"elementId": "user", "type": "textbox", "humanName": "Username", "technical": {"locatorCandidates": []}}]}],
        "meaningfulActions": [{"sequence": 1, "action": "Log in", "evidenceStatus": "OUTCOME_OBSERVED" if outcome else "DISCOVERED"}],
        "observedBehaviors": [], "conflicts": [], "gaps": [], "evidenceRefs": [],
    }


class FakeTool:
    def __init__(self, name):
        self.name, self.description, self.input_schema = name, name, {"type": "object", "properties": {}}


class Browser:
    """Stands in for the MCP server: records calls, answers with a page URL."""

    def __init__(self, page_url=URL, fail_close=False):
        self.calls, self.page_url, self.fail_close = [], page_url, fail_close
        self.tools = [FakeTool(n) for n in sorted(sc.allowed_tool_names(sc.ScanPolicy.INTERACTIVE))]

    async def initialize(self):
        pass

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "browser_close" and self.fail_close:
            raise RuntimeError("close failed")
        return SimpleNamespace(content=[SimpleNamespace(text=f"Page URL: {self.page_url}\nok: {json.dumps(arguments)}")], is_error=False)


class FakeApi:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def llm(self, job_id, messages, tools, tool_choice=None):
        self.requests.append({"messages": json.loads(json.dumps(messages)), "tools": [t["name"] for t in tools], "tool_choice": tool_choice})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"text": reply.get("text", ""), "tool_calls": reply.get("tool_calls", []), "tokens_in": 1, "tokens_out": 1}


def call(i, name, **arguments):
    return {"id": f"c{i}", "name": name, "arguments": arguments}


def job(**payload):
    base = {"summary": "User login", "description": "Username: standard_user, Password: secret_sauce", "acceptance_criteria": "Inventory page shows",
            "application_url": URL, "scan_policy": "INTERACTIVE", "test_environment_authorized": True}
    return {"id": "job-1", "payload": {**base, **payload}}


@pytest.fixture
def run(monkeypatch):
    def go(api, browser, jb=None, cancelled=lambda: False, reports=None):
        @asynccontextmanager
        async def fake_client(url):
            yield (None, None)

        class FakeSession:
            def __init__(self, r, w):
                pass

            async def __aenter__(self):
                return browser

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(scan_loop, "streamable_http_client", fake_client)
        monkeypatch.setattr(scan_loop, "ClientSession", FakeSession)
        return asyncio.run(scan_loop.run_scan(api, jb or job(), "http://fake/mcp", report=(reports.append if reports is not None else (lambda m: None)), cancelled=cancelled))

    return go


def test_normal_scan_returns_the_app_map_and_closes_the_browser(run):
    browser = Browser()
    reports = []
    api = FakeApi([
        {"tool_calls": [call(1, "browser_navigate", url=URL)]},
        {"tool_calls": [call(2, "browser_type", element="Password input", text="secret_sauce")]},
        {"text": "```json\n" + json.dumps(good_map()) + "\n```"},
    ])
    out = run(api, browser, reports=reports)
    assert out["status"] == "COMPLETE" and out["app_map"]["pages"][0]["name"] == "Login" and out["tool_call_count"] == 2
    assert browser.calls[-1] == ("browser_close", {})  # closed even though the model never closed it
    typed = out["history"][1]
    assert typed["arguments"]["text"] == "[REDACTED]" and "secret_sauce" not in json.dumps(out)  # password never leaves the runner
    assert any("browser_navigate" in r for r in reports) or reports == []
    first = api.requests[0]
    assert first["messages"][0]["role"] == "user" and "Requirement Ticket" in first["messages"][0]["content"] and "browser_click" in first["tools"]


def test_the_runner_never_sends_a_system_prompt_or_model_choice(run):
    api = FakeApi([{"text": json.dumps(good_map())}])
    run(api, Browser())
    assert set(api.requests[0]) == {"messages", "tools", "tool_choice"}


def test_unauthorized_interactive_scan_is_refused_without_touching_the_browser(run):
    browser = Browser()
    out = run(FakeApi([]), browser, jb=job(test_environment_authorized=False))
    assert out["status"] == "FAILED" and "authorized test environment" in out["stop_reason"] and browser.calls == []
    out = run(FakeApi([]), browser, jb=job(application_url="not a url"))
    assert out["status"] == "FAILED" and "valid http(s) URL" in out["stop_reason"] and browser.calls == []


def test_read_only_policy_blocks_typing_and_data_changing_clicks(run):
    browser = Browser()
    api = FakeApi([
        {"tool_calls": [call(1, "browser_type", element="Username", text="x"), call(2, "browser_click", element="Add to cart")]},
        {"text": json.dumps(good_map("PARTIAL"))},
    ])
    out = run(api, browser, jb=job(scan_policy="READ_ONLY"))
    assert [h["allowed"] for h in out["history"]] == [False, False]
    assert [c[0] for c in browser.calls] == ["browser_close"]  # nothing but the final close reached the browser
    assert "browser_type" not in api.requests[0]["tools"]


def test_origin_lock_stops_the_scan_when_the_browser_leaves_the_site(run):
    browser = Browser(page_url="https://evil.example/landing")
    api = FakeApi([{"tool_calls": [call(1, "browser_click", element="Some link")]}])
    out = run(api, browser)
    assert out["status"] == "PARTIAL" and "outside the authorized origin" in out["stop_reason"] and out["history"][0]["origin_violation"] is True
    assert api.requests and len(api.requests) == 1  # stopped at once: no further model calls, no wrap-up
    blocked = run(FakeApi([{"tool_calls": [call(1, "browser_navigate", url="https://evil.example/")]}, {"text": json.dumps(good_map("PARTIAL"))}]), Browser())
    assert blocked["history"][0]["allowed"] is False


def test_repeated_action_guard_then_wrap_up_saves_partial_map(run):
    browser = Browser()
    api = FakeApi([{"tool_calls": [call(i, "browser_snapshot")]} for i in range(4)] + [{"text": json.dumps(good_map("PARTIAL"))}])
    out = run(api, browser)
    assert "repeated 4 times" in out["stop_reason"] and out["status"] == "PARTIAL" and out["app_map"] is not None and "saved as a partial App Map" in out["stop_reason"]
    assert api.requests[-1]["tool_choice"] == "none"  # the wrap-up call forbids more tools
    assert api.requests[-1]["messages"][-1]["content"] == sc.WRAP_UP_MESSAGE


def test_budget_stop_answers_unexecuted_tool_calls_before_the_wrap_up(run, monkeypatch):
    monkeypatch.setattr(sc, "MAX_TOOL_CALLS", 1)
    api = FakeApi([{"tool_calls": [call(1, "browser_snapshot"), call(2, "browser_navigate", url=URL)]}, {"text": json.dumps(good_map("PARTIAL"))}])
    out = run(api, Browser())
    assert out["tool_call_count"] == 1 and "Operation limit reached" in out["stop_reason"]
    tool_msgs = [m for m in api.requests[-1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"] and "Not executed" in tool_msgs[1]["content"]  # valid request for both providers


def test_failed_wrap_up_keeps_a_clear_partial_result(run):
    api = FakeApi([{"tool_calls": [call(i, "browser_snapshot")]} for i in range(4)] + [{"text": "not json at all"}])
    out = run(api, Browser())
    assert out["app_map"] is None and "No App Map could be recovered" in out["stop_reason"] and out["status"] == "PARTIAL"


def test_complete_is_downgraded_without_outcome_evidence(run):
    out = run(FakeApi([{"text": json.dumps(good_map("COMPLETE", outcome=False))}]), Browser())
    assert out["status"] == "PARTIAL" and "Downgraded from COMPLETE" in out["stop_reason"]


def test_a_failing_browser_close_never_breaks_the_result(run):
    out = run(FakeApi([{"text": json.dumps(good_map())}]), Browser(fail_close=True))
    assert out["status"] == "COMPLETE"


def test_user_cancel_stops_the_scan(run):
    out = run(FakeApi([]), Browser(), cancelled=lambda: True)
    assert out["status"] == "FAILED" and out["stop_reason"] == "Scan cancelled by user."


def test_model_errors_map_to_clear_results_or_a_retry(run):
    out = run(FakeApi([ApiError(502, "llm_failed", "OpenAI rejected the server's API key.")]), Browser())
    assert out["status"] == "FAILED" and out["stop_reason"] == "OpenAI rejected the server's API key."
    with pytest.raises(scan_loop.RetryableScanError):
        run(FakeApi([ApiError(503, "llm_retryable", "OpenAI is rate limiting requests.")]), Browser())
    mid = run(FakeApi([{"tool_calls": [call(1, "browser_snapshot")]}, ApiError(503, "llm_retryable", "rate limited")]), Browser())
    assert mid["status"] == "PARTIAL" and mid["stop_reason"] == "rate limited"  # work already done is kept


def test_invalid_final_json_is_reported(run):
    out = run(FakeApi([{"text": "I could not finish"}]), Browser())
    assert out["status"] == "FAILED" and out["stop_reason"] == "The AI did not return a valid JSON App Map."


# ---- starting a scan (API) and saving the result ----------------------------
def setup_scan(monkeypatch=None):
    admin = new_client()
    signup_org(admin)
    pid = admin.post("/api/projects", json={"name": "Shop"}).json()["id"]
    return admin, pid


def add_requirement(admin, pid, monkeypatch):
    from app import llm
    from tests.test_requirements import REPLY, fake_llm, run_next, upload

    fake_llm(monkeypatch)
    upload(admin, pid)
    run_next()
    return admin.get(f"/api/projects/{pid}/requirements").json()["requirements"][0]


def scan_url(pid, rid):
    return f"/api/projects/{pid}/requirements/{rid}/scan"


def body(**over):
    return {"application_url": URL, "scan_policy": "INTERACTIVE", "test_environment_authorized": True, **over}


def test_start_scan_validation(monkeypatch):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    u = scan_url(pid, req["id"])
    assert admin.post(u, json=body(application_url="javascript:alert(1)")).json()["error"]["code"] == "bad_url"
    assert admin.post(u, json=body(application_url="https://a b")).status_code == 422
    assert admin.post(u, json=body(scan_policy="ANYTHING")).json()["error"]["code"] == "bad_policy"
    assert admin.post(u, json=body(test_environment_authorized=False)).json()["error"]["code"] == "authorization_required"
    ok_ro = admin.post(u, json=body(scan_policy="READ_ONLY", test_environment_authorized=False))
    assert ok_ro.json()["error"]["code"] == "no_runner_online"  # valid request, but nobody can run it
    assert admin.post(scan_url(pid, "00000000-0000-0000-0000-000000000000"), json=body()).status_code == 404
    assert new_client().post(u, json=body()).status_code == 401


def test_start_scan_needs_an_online_runner_and_a_provider_key(monkeypatch):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    u = scan_url(pid, req["id"])
    runner, _, _ = pair(admin)
    assert admin.post(u, json=body()).json()["error"]["code"] == "no_runner_online"  # paired but never seen
    runner.post("/api/runner/heartbeat", json={})
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert admin.post(u, json=body()).json()["error"]["code"] == "provider_not_configured"
    monkeypatch.undo()


def test_start_scan_fixes_model_and_requirement_text_in_the_job(monkeypatch, db):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    admin.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "tenant-model"})
    runner, _, _ = pair(admin)
    runner.post("/api/runner/heartbeat", json={})
    r = admin.post(scan_url(pid, req["id"]), json=body(description="Edited description. Password: hunter2xx"))
    assert r.status_code == 202
    j = db.scalar(select(Job).where(Job.type == "app_scan"))
    p = j.payload
    assert (p["provider"], p["model"], p["description"], p["application_url"], p["requirement_key"], p["prompt_sha256"]) == ("openai", "tenant-model", "Edited description. Password: hunter2xx", URL, "REQ-001", SCAN_PROMPT_SHA256)
    claimed = runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert claimed["type"] == "app_scan" and "system_prompt" not in claimed  # the runner is not given the prompt


def run_to_result(admin, pid, req, monkeypatch, result):
    runner, _, _ = pair(admin)
    runner.post("/api/runner/heartbeat", json={})
    started = admin.post(scan_url(pid, req["id"]), json=body(description="Login. Username: standard_user Password: secret_sauce")).json()
    claimed = runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert claimed["id"] == started["id"]
    done = runner.post(f"/api/runner/jobs/{started['id']}/complete", json={"result": result})
    return started["id"], done


def test_completed_scan_is_saved_validated_and_scrubbed(monkeypatch, db):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    leaky = good_map("COMPLETE")
    leaky["pages"][0]["controls"][0]["visibleContext"] = "typed secret_sauce here"
    result = {"status": "COMPLETE", "stop_reason": "Goal reached with secret_sauce", "app_map": leaky, "raw": "raw secret_sauce text", "schema_issues": [],
              "history": [{"sequence": 1, "tool": "browser_type", "arguments": {"text": "secret_sauce"}, "result": "typed secret_sauce"}], "tool_call_count": 7, "duration_seconds": 42}
    job_id, done = run_to_result(admin, pid, req, monkeypatch, result)
    assert done.json()["status"] == "succeeded"
    j = admin.get(f"/api/jobs/{job_id}").json()
    assert j["status"] == "succeeded" and j["result"]["pages"] == 1 and j["result"]["controls"] == 2 and j["result"]["tool_calls"] == 7
    scans = admin.get(f"/api/projects/{pid}/scans").json()["scans"]
    assert len(scans) == 1 and scans[0]["status"] == "COMPLETE" and scans[0]["requirement_key"] == "REQ-001" and scans[0]["controls"] == 2
    full = admin.get(f"/api/projects/{pid}/scans/{scans[0]['id']}").json()
    # scrubbed again on the server everywhere except the requirement text itself, which keeps what the user typed
    # (approved trade-off, same as Pilot 1: the login lives in the requirement description)
    without_snapshot = {k: v for k, v in full.items() if k != "requirement_snapshot"}
    assert "secret_sauce" not in json.dumps(without_snapshot) and "[REDACTED]" in json.dumps(without_snapshot)
    assert full["prompt_sha256"] == SCAN_PROMPT_SHA256 and full["duration_seconds"] == 42
    row = db.scalar(select(Scan))
    assert "secret_sauce" not in json.dumps(row.tsg_contract) and "secret_sauce" not in json.dumps(row.history)
    assert row.tsg_contract["verified_locators"][0]["human_name"] == "Login" and row.contract_issues == []
    reqs = admin.get(f"/api/projects/{pid}/requirements").json()["requirements"]
    assert reqs[0]["scan"] == {"id": scans[0]["id"], "status": "COMPLETE"}


def test_server_rederives_status_instead_of_trusting_the_runner(monkeypatch):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    liar = good_map("COMPLETE", outcome=False)
    _, done = run_to_result(admin, pid, req, monkeypatch, {"status": "COMPLETE", "stop_reason": "all good", "app_map": liar, "history": []})
    assert done.json()["status"] == "succeeded"
    s = admin.get(f"/api/projects/{pid}/scans").json()["scans"][0]
    assert s["status"] == "PARTIAL" and "Downgraded from COMPLETE" in s["stop_reason"]


def test_failed_scan_keeps_its_record_and_fails_the_job_with_the_reason(monkeypatch):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    hist = [{"sequence": 1, "tool": "browser_navigate", "result": "net::ERR_NAME_NOT_RESOLVED"}]
    job_id, done = run_to_result(admin, pid, req, monkeypatch, {"status": "FAILED", "stop_reason": "Could not open the site.", "app_map": None, "history": hist})
    assert done.json()["status"] == "failed"
    j = admin.get(f"/api/jobs/{job_id}").json()
    assert j["status"] == "failed" and j["error"] == "Could not open the site."
    s = admin.get(f"/api/projects/{pid}/scans").json()["scans"][0]
    assert s["status"] == "FAILED" and s["pages"] == 0
    assert admin.get(f"/api/projects/{pid}/scans/{s['id']}").json()["history"] == hist


def test_a_map_reported_without_status_is_treated_as_partial_and_junk_is_failed(monkeypatch):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    run_to_result(admin, pid, req, monkeypatch, {"status": "PARTIAL", "stop_reason": "", "app_map": None, "history": []})
    s = admin.get(f"/api/projects/{pid}/scans").json()["scans"][0]
    assert s["status"] == "FAILED" and s["stop_reason"] == "The scan returned no App Map."


def test_scans_are_isolated_between_organizations(monkeypatch):
    a, pid_a = setup_scan()
    req = add_requirement(a, pid_a, monkeypatch)
    run_to_result(a, pid_a, req, monkeypatch, {"status": "COMPLETE", "stop_reason": "x", "app_map": good_map(), "history": []})
    scan_id = a.get(f"/api/projects/{pid_a}/scans").json()["scans"][0]["id"]
    b = new_client()
    signup_org(b, org="Other Co", email="o@other.example")
    pid_b = b.post("/api/projects", json={"name": "Mine"}).json()["id"]
    assert b.get(f"/api/projects/{pid_a}/scans").status_code == 404
    assert b.get(f"/api/projects/{pid_a}/scans/{scan_id}").status_code == 404
    assert b.get(f"/api/projects/{pid_b}/scans/{scan_id}").status_code == 404  # right id, wrong project
    assert b.post(scan_url(pid_a, req["id"]), json=body()).status_code == 404


def test_cancel_job_permissions(monkeypatch):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    runner, _, _ = pair(admin)
    runner.post("/api/runner/heartbeat", json={})
    admin.post("/api/users", json={"name": "Una", "email": "una@acme.example", "password": GOOD_PASSWORD})
    una, other = new_client(), new_client()
    login(una, "una@acme.example")
    signup_org(other, org="Other Co", email="o@other.example")
    job_id = admin.post(scan_url(pid, req["id"]), json=body()).json()["id"]
    assert una.post(f"/api/jobs/{job_id}/cancel").status_code == 403  # not hers
    assert other.post(f"/api/jobs/{job_id}/cancel").status_code == 404
    assert admin.post(f"/api/jobs/{job_id}/cancel").json() == {"result": "cancelled"}
    assert admin.get(f"/api/jobs/{job_id}").json()["error"] == "Cancelled by the user."


# ---- models that cannot call tools (regression: a scan failed with "bad request" for gpt-6.1-sol) -----------------
from types import SimpleNamespace as _NS  # noqa: E402

import httpx as _httpx  # noqa: E402
import openai as _openai  # noqa: E402

from app import ai_providers as _ap  # noqa: E402
from app import llm as _llm  # noqa: E402

_REAL_CHECK = _ap.check_tool_calling  # captured before the autouse fixture stubs it
_TOOLS_ERROR = "Function tools with reasoning_effort are not supported for gpt-x in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'."


def _bad_request(message=_TOOLS_ERROR):
    req = _httpx.Request("POST", "https://api.example/v1/chat/completions")
    return _openai.BadRequestError("bad", response=_httpx.Response(400, request=req), body={"message": message, "type": "invalid_request_error"})


def test_bad_request_message_now_says_why_and_never_leaks_a_key():
    err = _llm._translate("openai", "m", _bad_request("Unsupported value for sk-ABCDEF1234567890 in this request"))
    assert err.message.startswith("OpenAI refused the request: ") and "Unsupported value" in err.message and "sk-ABCDEF" not in err.message
    assert _llm._translate("openai", "gpt-x", _bad_request()).message.endswith("set reasoning_effort to 'none'.")
    assert _llm.provider_reason(_openai.BadRequestError("plain text", response=_httpx.Response(400, request=_httpx.Request("POST", "https://x")), body=None)) == "plain text"


def test_tool_probe_detects_unsupported_models_and_caches(monkeypatch):
    _ap._TOOL_CACHE.clear()
    calls = []

    def refuse(**kw):
        calls.append(kw)
        raise _bad_request()

    # a model that refuses tools on chat completions AND on the Responses API
    client = _NS(chat=_NS(completions=_NS(create=refuse)), responses=_NS(create=refuse))
    monkeypatch.setattr(_ap, "_client", lambda p: client)
    ok, reason = _REAL_CHECK("openai", "gpt-x")
    assert ok is False and "reasoning_effort" in reason and calls[0]["tools"]
    assert len(calls) == 2  # tried chat completions, then the Responses API, then gave up
    assert _REAL_CHECK("openai", "gpt-x") == (False, reason) and len(calls) == 2  # cached: one probe per model per hour


def test_tool_probe_true_unknown_and_anthropic(monkeypatch):
    _ap._TOOL_CACHE.clear()
    good = _NS(choices=[_NS(message=_NS(content="ok", tool_calls=None))], usage=_NS(prompt_tokens=1, completion_tokens=1))
    ok_client = _NS(chat=_NS(completions=_NS(create=lambda **kw: good)))
    monkeypatch.setattr(_ap, "_client", lambda p: ok_client)
    assert _REAL_CHECK("openai", "good") == (True, "")
    flaky = _NS(chat=_NS(completions=_NS(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("network down")))))
    monkeypatch.setattr(_ap, "_client", lambda p: flaky)
    assert _REAL_CHECK("openai", "unknown-model") == (None, "")  # a failed probe never blocks anything
    assert _REAL_CHECK("anthropic", "claude-x") == (None, "")


def test_scan_is_refused_before_queueing_when_the_model_cannot_use_tools(monkeypatch, db):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    runner, _, _ = pair(admin)
    runner.post("/api/runner/heartbeat", json={})
    monkeypatch.setattr(_ap, "check_tool_calling", lambda provider, model: (False, "Function tools are not supported for this model."))
    r = admin.post(scan_url(pid, req["id"]), json=body())
    assert r.status_code == 400 and r.json()["error"]["code"] == "model_cannot_scan"
    msg = r.json()["error"]["message"]
    assert "system-default-model" in msg and "Function tools are not supported" in msg and "My Settings" in msg
    assert db.scalar(select(Job.id).where(Job.type == "app_scan")) is None  # nothing was queued
    monkeypatch.setattr(_ap, "check_tool_calling", lambda provider, model: (None, ""))
    assert admin.post(scan_url(pid, req["id"]), json=body()).status_code == 202  # "could not tell" does not block


def test_test_connection_warns_when_a_model_cannot_scan(monkeypatch):
    admin = new_client()
    signup_org(admin)

    class M:
        def retrieve(self, model):
            return {}

    monkeypatch.setattr(_ap, "_client", lambda p: _NS(models=M()))
    monkeypatch.setattr(_ap, "check_tool_calling", lambda provider, model: (False, "no tools here"))
    r = admin.post("/api/tenant/ai-test", json={"provider": "openai", "model": "gpt-x"}).json()
    assert r["ok"] is True and r["tool_calling"] is False and "cannot be used for app scans" in r["message"] and "requirement generation" in r["message"]
    monkeypatch.setattr(_ap, "check_tool_calling", lambda provider, model: (True, ""))
    r = admin.post("/api/tenant/ai-test", json={"provider": "openai", "model": "gpt-5.5"}).json()
    assert r["tool_calling"] is True and "cannot" not in r["message"]


def test_the_runner_says_so_when_the_browser_closes_and_the_long_answer_starts(run):
    reports = []
    api = FakeApi([{"tool_calls": [call(1, "browser_navigate", url=URL)]}, {"tool_calls": [call(2, "browser_close")]}, {"text": json.dumps(good_map())}])
    out = run(api, Browser(), reports=reports)
    assert out["status"] == "COMPLETE"
    assert reports[-1].startswith("Browser closed. The AI is now writing the App Map")  # the last thing shown before the quiet minute
