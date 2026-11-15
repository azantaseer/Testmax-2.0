"""Test case generation. The validator tests are ported from Pilot 1's tests/test_tcg_generation.py (same cases, same
expectations); the rest covers the Pilot 2 parts: prompt hash, the job, the API, tenant isolation and the Word file."""
import hashlib
import io
import json
from pathlib import Path

import pytest
from docx import Document as DocxDocument
from sqlalchemy import select

from app import jobs, llm
from app.engines import scan_core as sc
from app.engines import tcg
from app.engines import tcg as te
from app.llm_types import LLMError, LLMResult
from app.models_runner import Scan
from app.models_tests import TcCase, TcSet
from app.models_work import Job
from app.worker import new_session, run_job
from tests.conftest import new_client, signup_org

TCG_PROMPT_SHA256 = "b876f5cabaf6535d348cdb2bb2567c650f777adb585103daff4a738134b74ae7"
PILOT1_PROMPT = Path("D:/Projects/TestMax-Independent-Pilot/system-prompts/test-case-generation-v2.0.txt")

REQUIREMENT_ID = "20261031_092710_bc313a/REQ-002"
SCAN_RUN_ID = "20261031_092757_0e1179"


def _step(no=1, text="Click the \"Login\" button.", field="Login", data="—", expected="The inventory page is displayed.", element=None):
    return {"stepNo": no, "testStep": text, "fieldName": field, "testData": data, "expectedResult": expected, "sourceElementId": element}


def _case(**overrides):
    case = {
        "testCaseType": "POSITIVE",
        "title": "Verify login succeeds",
        "overview": "o",
        "preRequisites": ["A registered account exists."],
        "testObjective": "t",
        "expectedResult": "The user reaches the inventory page.",
        "steps": [_step(1, "Navigate to the application URL.", "—", "https://www.saucedemo.com/", "The login page is displayed."), _step(2)],
    }
    case.update(overrides)
    return case


def _distinct_case(n, **overrides):
    """A case whose steps differ from every other _distinct_case(n)."""
    steps = [_step(1, "Navigate to the application URL.", "—", "https://www.saucedemo.com/", "The login page is displayed."), _step(2, "Click the \"Login\" button.", "Login", f"variant {n}")]
    return _case(steps=steps, title=f"Verify variant {n}", **overrides)


def _payload(cases=None, **overrides):
    payload = {"requirementId": REQUIREMENT_ID, "scanRunId": SCAN_RUN_ID, "testCases": [_case()] if cases is None else cases, "notGenerated": []}
    payload.update(overrides)
    return payload


def _validate(payload, **kwargs):
    return te.validate_and_normalize(payload, REQUIREMENT_ID, SCAN_RUN_ID, kwargs.pop("known_element_ids", set()), kwargs.pop("technical_tokens", set()), **kwargs)


class TestUnspecifiedBehaviorIsNeverBlocked:
    """The core product behavior: a requirement's silence must not stop a
    case, and no readiness/clarification status exists any more."""

    def test_negative_case_with_qa_experience_expected_result_is_accepted(self):
        blank_password = _case(
            testCaseType="NEGATIVE",
            title="Verify login is blocked when the Password field is left blank",
            expectedResult="Login is rejected, the user stays on the login page and an error indicates the password is required.",
            steps=[
                _step(1, "Navigate to the application URL.", "—", "https://www.saucedemo.com/", "The login page is displayed."),
                _step(2, "Enter the \"Username\" field.", "Username", "standard_user", "The username is shown."),
                _step(3, "Clear the \"Password\" field.", "Password", "(empty)", "The Password field is empty."),
                _step(4, "Click the \"Login\" button.", "Login", "—", "Login is rejected and an error stating the password is required is displayed."),
            ],
        )
        result = _validate(_payload([_case(), blank_password]))
        assert result["ok"] is True
        assert [tc["id"] for tc in result["normalized"]["testCases"]] == ["TC-001", "TC-002"]

    def test_status_and_clarification_fields_are_not_part_of_the_output(self):
        result = _validate(_payload())
        case = result["normalized"]["testCases"][0]
        assert "status" not in case
        assert "addressedDesignIds" not in case
        assert "coverageAssessment" not in result["normalized"]

    def test_legacy_status_values_are_not_required_or_rejected(self):
        payload = _payload([_case(status="NEEDS_REVIEW")])
        assert _validate(payload)["ok"] is True


class TestStructuralRulesStillEnforced:
    def test_empty_test_case_list_is_rejected(self):
        result = _validate(_payload([]))
        assert result["ok"] is False and any("No test cases" in i for i in result["critical_issues"])

    def test_invalid_test_case_type_is_rejected(self):
        result = _validate(_payload([_case(testCaseType="ADDITIONAL")]))
        assert result["ok"] is False and any("testCaseType" in i for i in result["critical_issues"])

    def test_blank_expected_result_is_rejected(self):
        result = _validate(_payload([_case(expectedResult="")]))
        assert result["ok"] is False

    def test_step_must_open_with_an_approved_action_word(self):
        bad = _case(steps=[_step(1, "Navigate to the application URL."), _step(2, "Verify the cart badge.", "Cart badge")])
        result = _validate(_payload([bad]))
        assert result["ok"] is False and any("approved action verb" in i for i in result["critical_issues"])

    def test_case_needs_a_completing_interaction(self):
        navigation_only = _case(steps=[_step(1, "Navigate to the application URL."), _step(2, "Enter the \"Username\" field.", "Username", "standard_user")])
        result = _validate(_payload([navigation_only]))
        assert result["ok"] is False and any("completing interaction" in i for i in result["critical_issues"])

    def test_single_step_case_is_rejected(self):
        result = _validate(_payload([_case(steps=[_step(1)])]))
        assert result["ok"] is False

    def test_enter_step_requires_a_value(self):
        bad = _case(steps=[_step(1, "Enter the \"Username\" field.", "Username", "—"), _step(2)])
        assert _validate(_payload([bad]))["ok"] is False

    def test_documented_value_may_not_be_replaced_by_a_placeholder(self):
        placeholder = _case(steps=[_step(1, "Enter the \"Password\" field.", "Password", "<authorized valid password>"), _step(2)])
        result = _validate(_payload([placeholder]), documented_test_data={"password": "secret_sauce"})
        assert result["ok"] is False and any("placeholder" in i for i in result["critical_issues"])

    def test_deliberately_wrong_concrete_value_is_allowed(self):
        wrong = _case(steps=[_step(1, "Enter the \"Password\" field.", "Password", "wrong_password_1"), _step(2)])
        assert _validate(_payload([wrong]), documented_test_data={"password": "secret_sauce"})["ok"] is True

    def test_technical_identifier_in_field_name_is_rejected(self):
        leaky = _case(steps=[_step(1, "Enter the \"Username\" field.", "login-username", "standard_user"), _step(2)])
        result = _validate(_payload([leaky]), technical_tokens={"login-username"})
        assert result["ok"] is False

    def test_wrong_requirement_and_scan_ids_are_corrected_not_fatal(self):
        result = _validate(_payload(requirementId="other", scanRunId="other"))
        assert result["ok"] is True
        assert result["normalized"]["requirementId"] == REQUIREMENT_ID
        assert result["normalized"]["scanRunId"] == SCAN_RUN_ID

    def test_unknown_source_element_id_is_reported_as_gap_not_invented(self):
        case = _case(steps=[_step(1, element="login-nonexistent"), _step(2)])
        result = _validate(_payload([case]), known_element_ids={"login-username"})
        assert result["ok"] is True
        assert any("login-nonexistent" in gap for gap in result["mapping_gaps"])
        assert result["normalized"]["testCases"][0]["steps"][0]["sourceElementId"] == "login-nonexistent"

    def test_steps_are_renumbered_deterministically(self):
        case = _case(steps=[_step(5), _step(9, "Click the \"Add to cart\" button.", "Add to cart")])
        steps = _validate(_payload([case]))["normalized"]["testCases"][0]["steps"]
        assert [s["stepNo"] for s in steps] == [1, 2]


class TestDuplicateGuardAndKeyNames:
    def test_case_with_identical_steps_is_dropped_and_reported(self):
        first = _case(title="Verify login succeeds")
        twin = _case(title="Verify login works (same steps, different title)")
        distinct = _case(title="Verify something else", steps=[_step(1), _step(2, "Click the \"Add to cart\" button.", "Add to cart")])
        result = _validate(_payload([first, twin, distinct]))
        assert result["ok"] is True
        titles = [tc["title"] for tc in result["normalized"]["testCases"]]
        assert titles == ["Verify login succeeds", "Verify something else"]
        assert [tc["id"] for tc in result["normalized"]["testCases"]] == ["TC-001", "TC-002"]
        assert any("Dropped duplicate" in w for w in result["warnings"])

    def test_same_steps_with_different_test_data_are_not_duplicates(self):
        a = _case(steps=[_step(1, "Enter the \"Username\" field.", "Username", "standard_user"), _step(2)])
        b = _case(title="Verify unknown user", steps=[_step(1, "Enter the \"Username\" field.", "Username", "nobody"), _step(2)])
        assert len(_validate(_payload([a, b]))["normalized"]["testCases"]) == 2

    def test_press_the_enter_key_is_not_flagged_as_two_actions(self):
        case = _case(steps=[_step(1, "Press the Enter key in the \"Password\" field.", "Password", "Enter"), _step(2)])
        result = _validate(_payload([case]))
        assert result["ok"] is True
        assert not any("more than one action" in w for w in result["warnings"])

    def test_genuinely_chained_actions_still_warn(self):
        case = _case(steps=[_step(1, "Enter the username and click the Login button.", "Username", "standard_user"), _step(2)])
        assert any("more than one action" in w for w in _validate(_payload([case]))["warnings"])


class TestNotGenerated:
    def test_missing_scan_element_is_reported_in_not_generated(self):
        payload = _payload(notGenerated=[{"title": "Verify removing a product", "missingElement": "Remove button", "reason": "never scanned"}])
        result = _validate(payload)
        assert result["ok"] is True
        assert result["normalized"]["notGenerated"] == [{"title": "Verify removing a product", "missingElement": "Remove button", "reason": "never scanned"}]
        assert te.summarize_coverage(result["normalized"])["not_generated_count"] == 1


class TestCoverageSummaryIsComputedNotSelfReported:
    def test_counts_per_category(self):
        cases = [_distinct_case(1), _distinct_case(2, testCaseType="NEGATIVE"), _distinct_case(3, testCaseType="NEGATIVE"), _distinct_case(4, testCaseType="EDGE")]
        summary = te.summarize_coverage(_validate(_payload(cases))["normalized"])
        assert summary["type_counts"] == {"POSITIVE": 1, "NEGATIVE": 2, "VERIFICATION": 0, "BOUNDARY": 0, "EDGE": 1}
        assert summary["test_case_count"] == 4



# ===========================================================================
# Pilot 2 parts
# ===========================================================================
URL = "https://shop.example.test/"


def test_tcg_prompt_is_unchanged_from_pilot1():
    text, sha = tcg.load_system_prompt()
    assert sha == TCG_PROMPT_SHA256
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == sha
    if PILOT1_PROMPT.exists():
        assert hashlib.sha256(PILOT1_PROMPT.read_bytes()).hexdigest() == sha
    for removed in ("PRESERVE-ALL", "EXPLORATORY", "NEEDS_SOURCE_INFORMATION"):
        assert removed not in text
    assert "notGenerated" in text and "NO OVERLAPPING CASES" in text and "COVERAGE CHECK" in text


def app_map():
    return {
        "requirementId": "REQ-001", "targetUrl": URL, "mode": "INTERACTIVE", "status": "COMPLETE", "stopReason": "done", "goalReached": True,
        "pages": [{"url": URL, "name": "Login", "controls": [
            {"elementId": "login-btn", "type": "button", "humanName": "Login", "technical": {"locatorCandidates": [{"locator": "page.getByRole('button', { name: 'Login' })", "verification": "VERIFIED"}]}},
            {"elementId": "login-username", "type": "textbox", "humanName": "Username", "technical": {"locatorCandidates": []}}]}],
        "meaningfulActions": [{"sequence": 1, "action": "Log in", "evidenceStatus": "OUTCOME_OBSERVED"}],
        "observedBehaviors": [], "conflicts": [], "gaps": [], "evidenceRefs": [],
    }


def make_scan(db, tenant_id, project_id, requirement_id, status="COMPLETE", description="Login. Username: standard_user Password: secret_sauce"):
    amap = app_map()
    snapshot = {"summary": "Login", "description": description, "acceptance_criteria": "User lands on the inventory page."}
    record = {"run_id": "scan", "requirement_id": str(requirement_id), "application_url": URL, "model": "m", "scan_policy": "INTERACTIVE", "status": status}
    scan = Scan(
        tenant_id=tenant_id, project_id=project_id, requirement_id=requirement_id, application_url=URL, scan_policy="INTERACTIVE", status=status,
        provider="openai", model="m", requirement_snapshot=snapshot, app_map=amap,
        tcg_contract=sc.build_tcg_input_contract(record, snapshot, amap), tsg_contract=sc.build_tsg_input_contract(record, amap),
    )
    db.add(scan)
    db.commit()
    return scan


def seed(monkeypatch, db, name="Acme Corp", email="owner@acme.example", with_scan=True):
    from tests.test_scan import add_requirement

    admin = new_client()
    assert signup_org(admin, org=name, email=email).status_code == 201
    pid = admin.post("/api/projects", json={"name": "Shop"}).json()["id"]
    req = add_requirement(admin, pid, monkeypatch)
    scan = None
    if with_scan:
        from app.models import Project

        project = db.get(Project, pid)
        scan = make_scan(db, project.tenant_id, project.id, req["id"])
    return admin, pid, req, scan


def two_cases(**over):
    cases = [
        {"testCaseType": "POSITIVE", "title": "Verify a valid user can log in", "overview": "A registered user logs in.", "preRequisites": ["A registered account exists."],
         "testObjective": "Confirm login works.", "expectedResult": "The inventory page is shown.",
         "steps": [_step(1, "Navigate to the application URL.", "—", URL, "The login page is displayed."),
                   _step(2, "Enter the \"Username\" field.", "Username", "standard_user", "The username is shown.", "login-username"),
                   _step(3, "Click the \"Login\" button.", "Login", "—", "The inventory page is displayed.", "login-btn")]},
        {"testCaseType": "NEGATIVE", "title": "Verify login fails without a password", "overview": "o", "preRequisites": [],
         "testObjective": "Confirm an error.", "expectedResult": "An error is shown.",
         "steps": [_step(1, "Navigate to the application URL.", "—", URL, "The login page is displayed."),
                   _step(2, "Click the \"Login\" button.", "Login", "—", "An error is displayed.", "login-btn")]},
    ]
    return {"requirementId": "x", "scanRunId": "y", "testCases": cases, "notGenerated": [{"title": "Verify logout", "missingElement": "Logout", "reason": "never scanned"}], **over}


def fake_converse(monkeypatch, replies, calls=None):
    queue = list(replies)

    def converse(provider, model, system, messages, max_output_tokens=None, client=None):
        if calls is not None:
            calls.append({"provider": provider, "model": model, "system": system, "messages": [dict(m) for m in messages], "max": max_output_tokens})
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, LLMResult):
            return reply
        return LLMResult(text=reply if isinstance(reply, str) else json.dumps(reply), tokens_in=1000, tokens_out=2000)

    monkeypatch.setattr(llm, "converse", converse)


def gen_url(pid, rid):
    return f"/api/projects/{pid}/requirements/{rid}/test-cases/generate"


def run_next(worker="t-worker"):
    with new_session() as s:
        jid = jobs.claim(s, worker, ["generate_test_cases"])
    assert jid is not None
    return jid, run_job(jid, worker)


def job_row(jid):
    with new_session() as s:
        return s.get(Job, jid)


def test_plan_and_start_need_a_scan_of_the_same_requirement(monkeypatch, db):
    from app.models import Project
    from app.models_work import Requirement

    admin, pid, req, _ = seed(monkeypatch, db, with_scan=False)
    plan = admin.get(f"/api/projects/{pid}/requirements/{req['id']}/test-cases/plan").json()
    assert plan["scan"] is None and plan["requirement_key"] == "REQ-001"
    r = admin.post(gen_url(pid, req["id"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_scan"
    # a scan of ANOTHER requirement must never be used (Pilot 1 refuses to combine them)
    project = db.get(Project, pid)
    reqs = db.scalars(select(Requirement).where(Requirement.project_id == project.id).order_by(Requirement.seq)).all()
    make_scan(db, project.tenant_id, project.id, reqs[1].id)
    assert admin.post(gen_url(pid, req["id"])).json()["error"]["code"] == "no_scan"
    assert admin.post(gen_url(pid, str(reqs[1].id))).status_code == 202


def test_failed_or_map_less_scans_are_not_used(monkeypatch, db):
    admin, pid, req, scan = seed(monkeypatch, db)
    scan.status = "FAILED"
    db.commit()
    assert admin.post(gen_url(pid, req["id"])).json()["error"]["code"] == "no_scan"
    scan.status, scan.app_map = "PARTIAL", None
    db.commit()
    assert admin.post(gen_url(pid, req["id"])).json()["error"]["code"] == "no_scan"


def test_generation_saves_numbered_cases_and_everything_around_them(monkeypatch, db):
    admin, pid, req, scan = seed(monkeypatch, db)
    calls = []
    fake_converse(monkeypatch, [two_cases()], calls)
    started = admin.post(gen_url(pid, req["id"]))
    assert started.status_code == 202
    jid, state = run_next()
    assert state == "succeeded"
    assert len(calls) == 1
    sent = calls[0]
    assert sent["system"] == tcg.load_system_prompt()[0]  # the unchanged prompt
    assert sent["max"] == tcg.MAX_OUTPUT_TOKENS >= 64000
    user_message = sent["messages"][0]["content"]
    assert "Requirement ID: REQ-001" in user_message and "Normalized App Scan (JSON)" in user_message and str(scan.id) in user_message
    assert "Password: secret_sauce" in user_message  # the requirement's own documented data reaches the model
    j = admin.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "succeeded" and j["result"]["test_case_count"] == 2 and j["result"]["coverage"]["type_counts"]["NEGATIVE"] == 1
    assert (job_row(jid).tokens_in, job_row(jid).tokens_out) == (1000, 2000)

    rows = admin.get(f"/api/projects/{pid}/test-cases").json()
    assert rows["total"] == 2 and [c["key"] for c in rows["test_cases"]] == ["TC-001", "TC-002"]
    assert rows["test_cases"][0]["requirement_key"] == "REQ-001" and rows["test_cases"][0]["type"] == "POSITIVE"
    detail = admin.get(f"/api/projects/{pid}/test-cases/{rows['test_cases'][0]['id']}").json()
    assert [s["stepNo"] for s in detail["steps"]] == [1, 2, 3] and detail["steps"][1]["sourceElementId"] == "login-username"
    assert detail["prerequisites"] == ["A registered account exists."] and detail["objective"] == "Confirm login works."
    s = detail["set"]
    assert s["prompt_sha256"] == TCG_PROMPT_SHA256 and s["scan_id"] == str(scan.id) and s["coverage"]["test_case_count"] == 2
    assert s["not_generated"][0]["missingElement"] == "Logout" and s["attempts"] == 1

    # a second generation continues the numbering; the first cases stay
    fake_converse(monkeypatch, [two_cases()])
    admin.post(gen_url(pid, req["id"]))
    run_next()
    keys = [c["key"] for c in admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"]]
    assert keys == ["TC-001", "TC-002", "TC-003", "TC-004"]
    assert len(admin.get(f"/api/projects/{pid}/test-case-sets", params={"requirement_id": req["id"]}).json()["sets"]) == 2
    first = admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][0]["id"]
    assert admin.delete(f"/api/projects/{pid}/test-cases/{first}").status_code == 204
    assert admin.get(f"/api/projects/{pid}/test-cases/{first}").status_code == 404


def test_search_sort_and_paging(monkeypatch, db):
    admin, pid, req, _ = seed(monkeypatch, db)
    fake_converse(monkeypatch, [two_cases()])
    admin.post(gen_url(pid, req["id"]))
    run_next()
    base = f"/api/projects/{pid}/test-cases"
    assert admin.get(base, params={"q": "without a password"}).json()["total"] == 1
    assert admin.get(base, params={"q": "100%_x"}).json()["total"] == 0
    assert [c["key"] for c in admin.get(base, params={"sort": "key", "direction": "desc"}).json()["test_cases"]] == ["TC-002", "TC-001"]
    assert len(admin.get(base, params={"page_size": 1, "page": 2}).json()["test_cases"]) == 1


def test_two_bad_answers_fail_the_job_with_the_reason(monkeypatch, db):
    admin, pid, req, _ = seed(monkeypatch, db)
    calls = []
    bad = two_cases()
    bad["testCases"][0]["expectedResult"] = ""
    fake_converse(monkeypatch, [bad, "not json at all", two_cases()], calls)
    admin.post(gen_url(pid, req["id"]))
    jid, state = run_next()
    assert state == "failed" and len(calls) == 2  # one request plus one bounded retry, as in Pilot 1
    assert [m["role"] for m in calls[1]["messages"]] == ["user", "assistant", "user"]
    assert "failed schema validation" in calls[1]["messages"][-1]["content"]
    assert "valid JSON" in job_row(jid).error
    assert admin.get(f"/api/projects/{pid}/test-cases").json()["total"] == 0


def test_validation_retry_succeeds_and_both_requests_are_metered(monkeypatch, db):
    admin, pid, req, _ = seed(monkeypatch, db)
    calls = []
    bad = two_cases()
    bad["testCases"][0]["expectedResult"] = ""
    fake_converse(monkeypatch, [bad, two_cases()], calls)
    admin.post(gen_url(pid, req["id"]))
    jid, state = run_next()
    assert state == "succeeded" and len(calls) == 2
    assert "expectedResult" in calls[1]["messages"][-1]["content"]
    case = admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][0]
    assert admin.get(f"/api/projects/{pid}/test-cases/{case['id']}").json()["set"]["attempts"] == 2
    assert (job_row(jid).tokens_in, job_row(jid).tokens_out) == (2000, 4000)


def test_cut_off_answer_fails_clearly_without_a_second_paid_request(monkeypatch, db):
    admin, pid, req, _ = seed(monkeypatch, db)
    calls = []
    fake_converse(monkeypatch, [LLMResult('{"testCases": [ {"title": "cut', 1000, 96000, truncated=True)], calls)
    admin.post(gen_url(pid, req["id"]))
    jid, state = run_next()
    assert state == "failed" and len(calls) == 1
    assert "ran out of output space" in job_row(jid).error
    assert admin.get(f"/api/projects/{pid}/test-cases").json()["total"] == 0


def test_provider_errors_are_retried_or_reported(monkeypatch, db):
    admin, pid, req, _ = seed(monkeypatch, db)
    fake_converse(monkeypatch, [LLMError("OpenAI had a temporary error.", retryable=True), LLMError("The OpenAI key was rejected.")])
    admin.post(gen_url(pid, req["id"]))
    jid, state = run_next()
    assert state == "requeued"
    jid, state = run_next()
    assert state == "failed" and job_row(jid).error == "The OpenAI key was rejected."


def test_one_generation_per_requirement_at_a_time_and_model_is_fixed_at_start(monkeypatch, db):
    admin, pid, req, _ = seed(monkeypatch, db)
    admin.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "tenant-model"})
    first = admin.post(gen_url(pid, req["id"]))
    assert first.status_code == 202
    assert admin.post(gen_url(pid, req["id"])).json()["error"]["code"] == "already_running"
    plan = admin.get(f"/api/projects/{pid}/requirements/{req['id']}/test-cases/plan").json()
    assert plan["running_job"]["id"] == first.json()["id"] and plan["model"] == "tenant-model"
    admin.put("/api/tenant/ai-settings", json={"provider": "anthropic", "model": "changed-later"})
    calls = []
    fake_converse(monkeypatch, [two_cases()], calls)
    run_next()
    assert (calls[0]["provider"], calls[0]["model"]) == ("openai", "tenant-model")


def test_handler_refuses_a_scan_of_a_different_requirement(monkeypatch, db):
    from app.models import Project
    from app.models_work import Requirement

    admin, pid, req, scan = seed(monkeypatch, db)
    project = db.get(Project, pid)
    other_req = db.scalars(select(Requirement).where(Requirement.project_id == project.id, Requirement.seq == 2)).one()
    other_scan = make_scan(db, project.tenant_id, project.id, other_req.id)
    jobs.enqueue(db, tenant_id=project.tenant_id, job_type="generate_test_cases", project_id=project.id,
                 payload={"requirement_id": str(req["id"]), "scan_id": str(other_scan.id), "provider": "openai", "model": "m"})
    calls = []
    fake_converse(monkeypatch, [two_cases()], calls)
    jid, state = run_next()
    assert state == "failed" and calls == [] and "different requirement" in job_row(jid).error


def test_other_organizations_see_nothing_and_change_nothing(monkeypatch, db):
    admin, pid, req, _ = seed(monkeypatch, db)
    fake_converse(monkeypatch, [two_cases()])
    admin.post(gen_url(pid, req["id"]))
    run_next()
    case = admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][0]
    set_id = case["set_id"]
    other = new_client()
    assert signup_org(other, org="Other Inc", email="boss@other.example").status_code == 201
    opid = other.post("/api/projects", json={"name": "Theirs"}).json()["id"]
    for url in (f"/api/projects/{pid}/test-cases", f"/api/projects/{pid}/test-cases/{case['id']}", f"/api/projects/{pid}/test-case-sets",
                f"/api/projects/{pid}/test-case-sets/{set_id}/docx", f"/api/projects/{pid}/requirements/{req['id']}/test-cases/plan"):
        assert other.get(url).status_code == 404, url
    assert other.post(gen_url(pid, req["id"])).status_code == 404
    assert other.delete(f"/api/projects/{pid}/test-cases/{case['id']}").status_code == 404
    # their own project, but our ids
    assert other.get(f"/api/projects/{opid}/test-cases/{case['id']}").status_code == 404
    assert other.get(f"/api/projects/{opid}/test-case-sets/{set_id}/docx").status_code == 404
    assert other.get(f"/api/projects/{opid}/test-cases").json()["total"] == 0
    assert other.post(gen_url(opid, req["id"])).status_code == 404
    assert new_client().get(f"/api/projects/{pid}/test-cases").status_code == 401
    assert admin.get(f"/api/projects/{pid}/test-cases/{case['id']}").status_code == 200  # still there


def test_word_file_has_every_case_step_and_the_not_generated_list(monkeypatch, db):
    admin, pid, req, _ = seed(monkeypatch, db)
    fake_converse(monkeypatch, [two_cases()])
    admin.post(gen_url(pid, req["id"]))
    run_next()
    set_id = admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][0]["set_id"]
    r = admin.get(f"/api/projects/{pid}/test-case-sets/{set_id}/docx")
    assert r.status_code == 200 and "wordprocessingml" in r.headers["content-type"] and ".docx" in r.headers["content-disposition"]
    doc = DocxDocument(io.BytesIO(r.content))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "TC-001 — POSITIVE" in text and "Verify a valid user can log in" in text and "Verify logout" in text
    table_text = " ".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
    assert "standard_user" in table_text and "Click the \"Login\" button." in table_text


# ---- the provider adapters: output limit and cut-off detection -------------------
class FakeOpenAIClient:
    def __init__(self, finish="stop"):
        from types import SimpleNamespace as NS

        self.kwargs = None
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.kwargs = kwargs
                return NS(choices=[NS(message=NS(content="hello"), finish_reason=finish)], usage=NS(prompt_tokens=5, completion_tokens=7))

        self.chat = NS(completions=Completions())


def test_openai_adapter_passes_max_completion_tokens_and_flags_a_cut_off_reply():
    client = FakeOpenAIClient("length")
    result = llm.converse("openai", "m", "sys", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "a"}, {"role": "user", "content": "again"}], 96000, client=client)
    assert client.kwargs["max_completion_tokens"] == 96000
    assert [m["role"] for m in client.kwargs["messages"]] == ["system", "user", "assistant", "user"]
    assert result.truncated is True and (result.tokens_in, result.tokens_out) == (5, 7)
    client = FakeOpenAIClient()
    result = llm.complete("openai", "m", "sys", "hi", client=client)
    assert "max_completion_tokens" not in client.kwargs and result.truncated is False  # requirements keep their old call


class FakeAnthropicClient:
    def __init__(self, stop="end_turn"):
        from types import SimpleNamespace as NS

        self.kwargs = None
        outer = self

        class Stream:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def get_final_message(self_inner):
                return NS(content=[NS(type="text", text="hello")], usage=NS(input_tokens=3, output_tokens=4), stop_reason=stop)

        class Messages:
            def stream(self, **kwargs):
                outer.kwargs = kwargs
                return Stream()

        self.messages = Messages()


def test_anthropic_adapter_caps_the_output_limit_and_flags_a_cut_off_reply():
    from app.config import get_settings

    ceiling = get_settings().anthropic_max_output_tokens
    client = FakeAnthropicClient("max_tokens")
    result = llm.converse("anthropic", "m", "sys", [{"role": "user", "content": "hi"}], ceiling * 3, client=client)
    assert client.kwargs["max_tokens"] == ceiling and client.kwargs["system"] == "sys" and result.truncated is True
    client = FakeAnthropicClient()
    result = llm.converse("anthropic", "m", "sys", [{"role": "user", "content": "hi"}], 4000, client=client)
    assert client.kwargs["max_tokens"] == 4000 and result.truncated is False and result.text == "hello"
