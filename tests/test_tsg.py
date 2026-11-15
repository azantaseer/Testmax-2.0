"""Test script generation. The locator, validator and script-assembly tests are ported from Pilot 1's
tests/test_tsg_generation.py (same cases, same expectations); the rest covers the Pilot 2 parts: prompt hash, the job,
the API, tenant isolation, concurrency and the Node syntax check."""
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app import jobs, llm
from app.engines import tsg
from app.engines import tsg as ts
from app.llm_types import LLMError, LLMResult
from app.models_tests import TcCase, TsScript
from app.models_work import Job
from app.worker import new_session, run_job
from tests.conftest import new_client, signup_org

TSG_PROMPT_SHA256 = "5b3f31e2f586425f5b87d55c92c4c4c34e606d17484d10d2ada925c7abdcab99"
PILOT1_PROMPT = Path("D:/Projects/TestMax-Independent-Pilot/system-prompts/test-script-generation-v1.0.txt")

GENERATION_ID = "20261104_100000_abc123"


def _control(element_id, name, control_type="button", role="button", **technical):
    return {
        "elementId": element_id,
        "type": control_type,
        "humanName": name,
        "visibleContext": "main",
        "technical": {
            "role": role, "accessibleName": name, "domId": "", "domName": "", "testId": "",
            "locatorCandidates": [], **technical,
        },
    }


APP_MAP = {
    "targetUrl": "https://example.test/",
    "pages": [
        {
            "url": "https://example.test/login",
            "name": "Login",
            "controls": [
                _control(
                    "login-username", "Username", "textbox", "textbox",
                    testId="user-field",
                    locatorCandidates=[
                        {"locator": "page.getByRole('textbox', { name: 'Username' })", "verification": "UNVERIFIED", "uniquenessChecked": False},
                        {"locator": "page.locator('#user-name')", "verification": "VERIFIED", "uniquenessChecked": True},
                        {"locator": "page.locator('x'); process.exit(1)", "verification": "UNVERIFIED", "uniquenessChecked": False},
                    ],
                ),
                _control("login-button", "Login"),
                _control("inventory-cart", "Cart", "button", "button", accessibleName="Cart, empty", locatorCandidates=[
                    {"locator": "page.getByRole('button', { name: 'Cart, empty' })", "verification": "UNVERIFIED", "uniquenessChecked": False},
                ]),
            ],
        }
    ],
}


def _step(number, text, element_id, data="—", expected="ok"):
    return {"stepNo": number, "testStep": text, "fieldName": "x", "testData": data, "expectedResult": expected, "sourceElementId": element_id}


TEST_CASE = {
    "id": "TC-001",
    "testCaseType": "POSITIVE",
    "title": "Verify login works",
    "overview": "o",
    "preRequisites": ["p"],
    "testObjective": "t",
    "expectedResult": "The products page is displayed.",
    "steps": [
        _step(1, "Navigate to the application URL.", None, "https://example.test/login"),
        _step(2, 'Enter the "Username" field.', "login-username", "standard_user"),
        _step(3, 'Click the "Login" button.', "login-button"),
    ],
}

GOOD_BODY = """await test.step('Step 1: Navigate to the application URL.', async () => {
  await page.goto('https://example.test/login');
});
await test.step('Step 2: Enter the "Username" field.', async () => {
  await (await el('login-username')).fill('standard_user');
});
await test.step('Step 3: Click the "Login" button.', async () => {
  await (await el('login-button')).click();
  await expect(page).toHaveURL(/inventory/);
});"""


def test_locators_use_verified_first_then_scan_candidates_then_derived_fallbacks():
    candidates = ts.build_locator_candidates(APP_MAP["pages"][0]["controls"][0])
    assert candidates[0] == "page.locator('#user-name')"  # the verified one leads
    assert "page.getByRole('textbox', { name: 'Username' })" in candidates
    assert 'page.getByTestId("user-field")' in candidates
    assert any(c.startswith("page.getByLabel(") for c in candidates)
    assert len(candidates) <= ts.MAX_LOCATORS_PER_ELEMENT


def test_a_role_and_name_locator_gets_an_exact_name_twin_tried_first():
    """getByRole('button', { name: 'State' }) also matches 'United States'."""
    control = _control("state", "State", locatorCandidates=[
        {"locator": "page.getByRole('button', { name: 'State' })", "verification": "UNVERIFIED", "uniquenessChecked": False},
    ])
    candidates = ts.build_locator_candidates(control)
    assert candidates[0] == "page.getByRole('button', { name: 'State', exact: true })"
    assert candidates[1] == "page.getByRole('button', { name: 'State' })"


def test_a_field_without_an_accessible_name_is_never_given_a_guessed_role_and_name_locator():
    """The State dropdown has no accessible name; name: 'State' would also match 'United States'."""
    control = _control("state", "State", "button/combobox", "button", accessibleName="")
    candidates = ts.build_locator_candidates(control)
    assert not any("getByRole('button', { name:" in c or 'getByRole("button", { name:' in c for c in candidates)
    assert any(c.startswith("page.getByRole('group').filter({ has: page.getByText(/^State(?!\\w)/i) }).last().getByRole(") for c in candidates)


def test_label_proximity_locator_escapes_the_field_name():
    control = _control("x", "Save (now) / go", "button", "button", accessibleName="")
    proximity = [c for c in ts.build_locator_candidates(control) if ".last()" in c]
    assert proximity and "/^Save \\(now\\) \\/ go(?!\\w)/i" in proximity[0]
    assert ts.is_safe_locator_expression(proximity[0])


@pytest.mark.parametrize(
    "snippet",
    [
        "await expect(await el('login-button')).toHaveText('Login');",
        "await expect(page.locator('body')).toContainText('x');",
        "await expect(await el('login-username')).toHaveValue('a');",
        "await expect(page.getByText('Products')).toBeVisible();",
    ],
)
def test_brittle_text_and_value_assertions_are_rejected_in_favour_of_the_tolerant_helpers(snippet):
    issues = ts.validate_body(GOOD_BODY + "\n" + snippet, TEST_CASE, _elements())
    assert any("expectText" in issue for issue in issues), issues


@pytest.mark.parametrize(
    "snippet",
    [
        "const price = parseFloat('3.5');",
        "expect(price).toBeCloseTo(3.5, 2);",
        "await page.evaluate(() => document.title);",
        "const n = Number(x);",
    ],
)
def test_scraping_and_comparing_numbers_from_the_page_is_rejected(snippet):
    issues = ts.validate_body(GOOD_BODY + "\n" + snippet, TEST_CASE, _elements())
    assert any("numbers from the page" in issue for issue in issues), issues


def test_the_tolerant_helpers_use_the_text_users_see_and_ignore_formatting():
    helper = ts.SCRIPT_HELPER
    for name in ("async function expectText", "async function expectNoText", "async function expectValue"):
        assert name in helper
    assert "replace(/[^a-z0-9]/g, '')" in helper and "mainFrame" in helper


def test_a_dropdown_is_never_located_through_its_own_options_or_its_label_text():
    """The scan recorded OrangeHRM's 'User Role' dropdown by its options and by what it showed ('-- Select --')."""
    control = _control("role", "User Role", "dropdown", "listbox when expanded", accessibleName="", locatorCandidates=[
        {"locator": "page.locator('div').filter({ hasText: /^-- Select --$/ }).nth(2)", "verification": "UNVERIFIED", "uniquenessChecked": False},
        {"locator": "page.getByRole('option', { name: 'ESS' })", "verification": "VERIFIED", "uniquenessChecked": True},
    ])
    candidates = ts.build_locator_candidates(control)
    assert not any("getByRole('option'" in c for c in candidates)
    assert not any(c.startswith("page.getByText(\"User Role\"") for c in candidates)  # that is the label, not the control
    label_based = next(i for i, c in enumerate(candidates) if "following::" in c)
    state_based = next(i for i, c in enumerate(candidates) if "-- Select --" in c)
    assert label_based < state_based


def test_the_control_after_the_label_never_outranks_a_real_name():
    """Dynamite puts the floating label AFTER its input, so 'the control after the label' is the NEXT field."""
    control = _control("first", "First Name", "textbox", "textbox", accessibleName="First Name *", locatorCandidates=[
        {"locator": "page.getByRole('textbox', { name: 'First Name *' })", "verification": "UNVERIFIED", "uniquenessChecked": False},
    ])
    candidates = ts.build_locator_candidates(control)
    follow = next(i for i, c in enumerate(candidates) if "following::" in c)
    by_name = next(i for i, c in enumerate(candidates) if c.startswith("page.getByRole('textbox', { name: 'First Name *'"))
    assert by_name < follow


def test_position_and_text_dependent_locators_come_last():
    control = _control("sel", "Status", "dropdown", "listbox", accessibleName="", locatorCandidates=[
        {"locator": "page.locator('div').filter({ hasText: /^-- Select --$/ }).nth(3)", "verification": "UNVERIFIED", "uniquenessChecked": False},
    ])
    candidates = ts.build_locator_candidates(control)
    assert candidates[-1].endswith(".nth(3)")


def test_a_control_named_by_its_current_state_also_gets_a_stable_name_locator():
    control = _control("cart", "Cart", "link", "link", accessibleName="Cart, 1 items", locatorCandidates=[
        {"locator": "page.getByRole('link', { name: 'Cart, 1 items' })", "verification": "UNVERIFIED", "uniquenessChecked": False},
    ])
    candidates = ts.build_locator_candidates(control)
    assert "page.getByRole('link', { name: /^Cart(?!\\w)/i })" in candidates
    assert ts.check_syntax(ts.assemble_script(TEST_CASE, GENERATION_ID, {"cart": {"name": "Cart", "candidates": candidates}}, GOOD_BODY.replace("login-username", "cart").replace("login-button", "cart"))) is None


def test_the_state_free_prefix_ignores_names_without_state():
    assert ts._state_free_prefix("Cart, empty") == "Cart"
    assert ts._state_free_prefix("My cart, number of items: 0") == "My cart"
    assert ts._state_free_prefix("Login") is None


def test_empty_expected_values_and_native_selects_are_handled_by_expectvalue():
    helper = ts.SCRIPT_HELPER
    assert "expectEmpty" in helper and "selectedOptions" in helper




def test_the_helper_prefers_a_locator_that_matches_exactly_one_element():
    assert "matches === 1" in ts.SCRIPT_HELPER and "visible and unambiguous" in ts.SCRIPT_HELPER


def test_unsafe_scan_locators_never_reach_the_script():
    candidates = ts.build_locator_candidates(APP_MAP["pages"][0]["controls"][0])
    assert not any("process" in c or ";" in c for c in candidates)
    assert ts.is_safe_locator_expression("page.getByRole('link', { name: 'Home' })")
    assert not ts.is_safe_locator_expression("require('fs')")
    assert not ts.is_safe_locator_expression("page.locator(`${x}`)")


def test_only_elements_used_by_the_test_case_are_included():
    elements = ts.build_elements_for_test_case(TEST_CASE, ts.index_controls(APP_MAP))
    assert set(elements) == {"login-username", "login-button"}


# ---- body validation --------------------------------------------------------

def _elements():
    return ts.build_elements_for_test_case(TEST_CASE, ts.index_controls(APP_MAP))


def test_a_good_body_passes_validation():
    assert ts.validate_body(GOOD_BODY, TEST_CASE, _elements()) == []


@pytest.mark.parametrize(
    "bad_body, expected_fragment",
    [
        (GOOD_BODY.replace("el('login-button')", "el('made-up-id')"), "not a known scanned element"),
        (GOOD_BODY + "\nconst fs = require('fs');", "'require' is not allowed"),
        (GOOD_BODY + "\nawait page.waitForTimeout(3000);", "waitForTimeout"),
        (GOOD_BODY.replace("'standard_user'", "`standard_user`"), "Template literals"),
        (GOOD_BODY.replace("Step 2:", "Stage 2:"), "Step 2 has no test.step"),
        (GOOD_BODY.replace("await expect(page).toHaveURL(/inventory/);", ""), "no expect"),
        ("test('x', async () => {});\n" + GOOD_BODY, "Do not write test"),
        (GOOD_BODY.replace("el('login-button')", "el(buttonId)"), "plain quoted string"),
    ],
)
def test_bad_bodies_are_rejected(bad_body, expected_fragment):
    issues = ts.validate_body(bad_body, TEST_CASE, _elements())
    assert any(expected_fragment in issue for issue in issues), issues


def test_a_regular_expression_test_call_is_not_mistaken_for_a_test_wrapper():
    body = GOOD_BODY.replace("await expect(page).toHaveURL(/inventory/);", "expect(/inventory/i.test(token)).toBeTruthy();")
    assert ts.validate_body(body, TEST_CASE, _elements()) == []


@pytest.mark.parametrize(
    "snippet, expected_fragment",
    [
        ("if (true) { await expect(page).toHaveURL(/a/); }", "Do not branch"),
        ("expect(await page.getByText('x').isVisible()).toBeTruthy();", "immediate values"),
        ("expect(page.url()).toContain('a');", "immediate values"),
    ],
)
def test_non_waiting_checks_and_branching_are_rejected(snippet, expected_fragment):
    issues = ts.validate_body(GOOD_BODY + "\n" + snippet, TEST_CASE, _elements())
    assert any(expected_fragment in issue for issue in issues), issues


def test_a_state_bearing_control_name_in_a_locator_is_rejected():
    snippet = "const cart = page.getByRole('link', { name: 'Cart, empty' });"
    issues = ts.validate_body(GOOD_BODY + "\n" + snippet, TEST_CASE, _elements())
    assert any("current state" in issue for issue in issues), issues
    ok = ts.validate_body(GOOD_BODY + "\nconst cart = page.getByRole('link', { name: /^Cart/i });", TEST_CASE, _elements())
    assert not any("current state" in issue for issue in ok)


def test_forbidden_words_inside_test_data_strings_are_fine():
    body = GOOD_BODY.replace("standard_user", "process import fetch")
    assert ts.validate_body(body, TEST_CASE, _elements()) == []


def test_assembled_script_is_valid_javascript():
    script = ts.assemble_script(TEST_CASE, GENERATION_ID, _elements(), GOOD_BODY)
    assert ts.check_syntax(script) is None
    assert "require('@playwright/test')" in script and "const ELEMENTS" in script
    assert "TC-001 Verify login works" in script


def test_a_syntax_error_is_reported():
    assert "Syntax error" in ts.check_syntax("const x = ;")


# ---- ids ---------------------------------------------------------------------

def test_the_pointer_is_moved_away_after_navigation_so_hover_menus_do_not_cover_controls():
    script = ts.assemble_script(TEST_CASE, GENERATION_ID, _elements(), GOOD_BODY)
    assert "page.on('framenavigated'" in script and "page.mouse.move(" in script
    assert ts.check_syntax(script) is None


def test_the_same_locator_written_with_either_quote_style_is_listed_once():
    control = _control("x", "Save", locatorCandidates=[
        {"locator": "page.getByTestId('save')", "verification": "UNVERIFIED", "uniquenessChecked": False},
    ], testId="save")
    candidates = ts.build_locator_candidates(control)
    assert sum(1 for c in candidates if "getByTestId" in c) == 1


def test_only_unreplaced_placeholders_count_as_missing_test_data():
    case = {"steps": [
        {"testData": "<authorized password>"}, {"testData": "<authorized password>"}, {"testData": "<authorized username>"},
        {"testData": "<script>alert(1)</script>"}, {"testData": "Admin"}, {"testData": "—"},
    ]}
    assert ts.missing_test_data(case) == ["<authorized password>", "<authorized username>"]



# ===========================================================================
# Pilot 2 parts
# ===========================================================================
SHOP = "https://shop.example.test/"

BODY = """await test.step('Step 1: Navigate to the application URL.', async () => {
  await page.goto('https://shop.example.test/');
});
await test.step('Step 2: Enter the "Username" field.', async () => {
  await (await el('login-username')).fill('standard_user');
});
await test.step('Step 3: Click the "Login" button.', async () => {
  await (await el('login-btn')).click();
  await expectText(page, 'Products');
});"""

SKIP_BODY = "test.skip(true, 'Test data missing: <authorized password>');\n" + BODY


def test_tsg_prompt_is_unchanged_from_pilot1():
    text, sha = tsg.load_system_prompt()
    assert sha == TSG_PROMPT_SHA256
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == sha
    if PILOT1_PROMPT.exists():
        assert hashlib.sha256(PILOT1_PROMPT.read_bytes()).hexdigest() == sha
    assert "NEVER type a placeholder" in text and "test.skip(true" in text
    assert "Never join several labels" in text and "scope the check to that control" in text


def fake_script_converse(monkeypatch, replies, calls=None):
    queue = list(replies)

    def converse(provider, model, system, messages, max_output_tokens=None, client=None):
        if calls is not None:
            calls.append({"provider": provider, "model": model, "system": system, "messages": [dict(m) for m in messages], "max": max_output_tokens})
        reply = queue.pop(0) if len(queue) > 1 or not queue else queue[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, LLMResult):
            return reply
        return LLMResult(text=reply, tokens_in=500, tokens_out=700)

    monkeypatch.setattr(llm, "converse", converse)


def seed_cases(monkeypatch, db, **kw):
    """An organization with one requirement, one scan and two generated test cases (TC-001, TC-002)."""
    from tests.test_tcg import fake_converse, gen_url, run_next as run_tcg, two_cases, seed

    admin, pid, req, scan = seed(monkeypatch, db, **kw)
    fake_converse(monkeypatch, [two_cases()])
    admin.post(gen_url(pid, req["id"]))
    run_tcg()
    cases = admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"]
    return admin, pid, req, scan, {c["key"]: c["id"] for c in cases}


def start(admin, pid, *case_ids):
    return admin.post(f"/api/projects/{pid}/test-scripts/generate", json={"test_case_ids": list(case_ids)})


def run_script_job(worker="s-worker"):
    with new_session() as s:
        jid = jobs.claim(s, worker, ["generate_test_script"])
    assert jid is not None
    return jid, run_job(jid, worker)


def job_row(jid):
    with new_session() as s:
        return s.get(Job, jid)


def test_script_is_generated_checked_with_node_and_saved(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    calls = []
    fake_script_converse(monkeypatch, [BODY], calls)
    r = start(admin, pid, ids["TC-001"])
    assert r.status_code == 202 and len(r.json()["jobs"]) == 1 and r.json()["skipped"] == []
    jid, state = run_script_job()
    assert state == "succeeded" and len(calls) == 1
    sent = calls[0]
    assert sent["system"] == tsg.load_system_prompt()[0] and sent["max"] == tsg.MAX_OUTPUT_TOKENS
    user_message = sent["messages"][0]["content"]
    assert user_message.startswith("Convert this test case into the Playwright test body.")
    assert "page.getByRole" not in user_message and "login-username" in user_message  # names and ids only, never locator code
    assert "Verify a valid user can log in" in user_message and SHOP in user_message

    scripts = admin.get(f"/api/projects/{pid}/test-scripts").json()
    assert scripts["total"] == 1 and scripts["scripts"][0]["key"] == "TS-001" and scripts["scripts"][0]["test_case_key"] == "TC-001"
    one = admin.get(f"/api/projects/{pid}/test-scripts/{scripts['scripts'][0]['id']}").json()
    code = one["code"]
    assert ts.check_syntax(code) is None
    assert "const ELEMENTS" in code and "findElement" in code and "await el('login-username')" in code
    assert "TC-001 Verify a valid user can log in" in code
    assert one["elements_used"] == ["login-btn", "login-username"] and one["prompt_sha256"] == TSG_PROMPT_SHA256
    assert one["file_name"] == "TC-001.spec.js" and one["framework"] == "Playwright" and one["blocked_message"] is None
    assert (job_row(jid).tokens_in, job_row(jid).tokens_out) == (500, 700)
    # the test case now shows its script
    case = admin.get(f"/api/projects/{pid}/test-cases/{ids['TC-001']}").json()
    assert case["script"]["key"] == "TS-001"
    assert admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][1]["script"] is None
    d = admin.get(f"/api/projects/{pid}/test-scripts/{one['id']}/download")
    assert d.status_code == 200 and d.text == code and 'filename="TC-001.spec.js"' in d.headers["content-disposition"]


def test_invalid_first_answer_is_sent_back_with_the_reasons_then_accepted(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    calls = []
    bad = BODY.replace("await expectText(page, 'Products');", "if (true) { await page.waitForTimeout(500); }")
    fake_script_converse(monkeypatch, [bad, BODY], calls)
    start(admin, pid, ids["TC-001"])
    jid, state = run_script_job()
    assert state == "succeeded" and len(calls) == 2
    assert [m["role"] for m in calls[1]["messages"]] == ["user", "assistant", "user"]
    assert "failed these checks" in calls[1]["messages"][-1]["content"] and "waitForTimeout" in calls[1]["messages"][-1]["content"]
    sid = admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]["id"]
    assert admin.get(f"/api/projects/{pid}/test-scripts/{sid}").json()["attempts"] == 2
    assert (job_row(jid).tokens_in, job_row(jid).tokens_out) == (1000, 1400)


def test_a_syntax_error_goes_back_to_the_model_too(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    calls = []
    broken = BODY.replace("fill('standard_user');", "fill('standard_user';")
    fake_script_converse(monkeypatch, [broken, BODY], calls)
    start(admin, pid, ids["TC-001"])
    jid, state = run_script_job()
    assert state == "succeeded" and "Syntax error" in calls[1]["messages"][-1]["content"]


def test_two_rejected_answers_fail_cleanly_keep_them_for_inspection_and_save_nothing(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    calls = []
    fake_script_converse(monkeypatch, ["await page.waitForTimeout(100);"], calls)
    start(admin, pid, ids["TC-001"])
    jid, state = run_script_job()
    assert state == "failed" and len(calls) == 2
    row = job_row(jid)
    assert row.error.startswith("The generated script failed validation") and len(row.result["rejected_answers"]) == 2
    assert row.result["validation_issues"] and admin.get(f"/api/projects/{pid}/test-scripts").json()["total"] == 0
    assert (row.tokens_in, row.tokens_out) == (1000, 1400)


def test_cut_off_answer_and_provider_errors(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    fake_script_converse(monkeypatch, [LLMResult("await test.st", 10, 32000, truncated=True)])
    start(admin, pid, ids["TC-001"])
    jid, state = run_script_job()
    assert state == "failed" and "ran out of output space" in job_row(jid).error
    fake_script_converse(monkeypatch, [LLMError("OpenAI had a temporary error.", retryable=True), LLMError("The OpenAI key was rejected.")])
    start(admin, pid, ids["TC-001"])
    jid, state = run_script_job()
    assert state == "requeued"
    jid, state = run_script_job()
    assert state == "failed" and job_row(jid).error == "The OpenAI key was rejected."


def test_a_script_that_stops_for_missing_test_data_is_flagged_blocked(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    case = db.get(TcCase, ids["TC-001"])
    case.steps = [dict(s, testData="<authorized password>") if s["stepNo"] == 2 else s for s in case.steps]
    db.commit()
    fake_script_converse(monkeypatch, [SKIP_BODY])
    start(admin, pid, ids["TC-001"])
    jid, state = run_script_job()
    assert state == "succeeded" and job_row(jid).result["blocked"] is True
    s = admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]
    assert s["blocked"] is True
    one = admin.get(f"/api/projects/{pid}/test-scripts/{s['id']}").json()
    assert "<authorized password>" in one["blocked_message"] and "Scan App" in one["blocked_message"]
    assert admin.get(f"/api/projects/{pid}/test-cases/{ids['TC-001']}").json()["script"]["blocked"] is True


def test_not_automated_notes_and_steps_without_a_scanned_control_are_reported(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    body = BODY.replace("await expectText(page, 'Products');", "// NOT AUTOMATED: the cart count cannot be read reliably\n  await expectText(page, 'Products');")
    fake_script_converse(monkeypatch, [body])
    start(admin, pid, ids["TC-002"])
    run_script_job()
    s = admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]
    one = admin.get(f"/api/projects/{pid}/test-scripts/{s['id']}").json()
    assert one["not_automated"] == ["the cart count cannot be read reliably"]


def test_regenerating_replaces_the_script_in_place(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    fake_script_converse(monkeypatch, [BODY])
    start(admin, pid, ids["TC-001"])
    run_script_job()
    first = admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]
    fake_script_converse(monkeypatch, [BODY.replace("'Products'", "'Swag Labs'")])
    start(admin, pid, ids["TC-001"])
    run_script_job()
    rows = admin.get(f"/api/projects/{pid}/test-scripts").json()
    assert rows["total"] == 1 and rows["scripts"][0]["id"] == first["id"] and rows["scripts"][0]["key"] == "TS-001"
    assert "Swag Labs" in admin.get(f"/api/projects/{pid}/test-scripts/{first['id']}").json()["code"]
    # a second case gets the next number
    start(admin, pid, ids["TC-002"])
    run_script_job()
    assert [s["key"] for s in admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"]] == ["TS-001", "TS-002"]


def test_deleting_a_test_case_deletes_its_script(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    fake_script_converse(monkeypatch, [BODY])
    start(admin, pid, ids["TC-001"])
    run_script_job()
    assert admin.delete(f"/api/projects/{pid}/test-cases/{ids['TC-001']}").status_code == 204
    assert admin.get(f"/api/projects/{pid}/test-scripts").json()["total"] == 0


def test_start_validation_duplicates_and_a_missing_scan(monkeypatch, db):
    from app.models_runner import Scan

    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    base = f"/api/projects/{pid}/test-scripts/generate"
    assert admin.post(base, json={"test_case_ids": []}).status_code == 422
    assert admin.post(base, json={"test_case_ids": ["00000000-0000-0000-0000-000000000000"]}).status_code == 404
    first = start(admin, pid, ids["TC-001"])
    again = start(admin, pid, ids["TC-001"], ids["TC-002"])
    assert len(again.json()["jobs"]) == 1 and again.json()["skipped"][0]["key"] == "TC-001" and "already being generated" in again.json()["skipped"][0]["reason"]
    # the scan is deleted: clear message, nothing queued
    with new_session() as sess:
        sess.execute(Job.__table__.update().values(status="failed"))
        sess.commit()
    db.delete(db.get(Scan, scan.id))
    db.commit()
    gone = start(admin, pid, ids["TC-001"])
    assert gone.json()["jobs"] == [] and "gone" in gone.json()["skipped"][0]["reason"]
    assert first.status_code == 202


def test_job_fails_clearly_when_the_scan_disappears_after_queueing(monkeypatch, db):
    from app.models_runner import Scan

    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    start(admin, pid, ids["TC-001"])
    db.delete(db.get(Scan, scan.id))
    db.commit()
    calls = []
    fake_script_converse(monkeypatch, [BODY], calls)
    jid, state = run_script_job()
    assert state == "failed" and calls == [] and "scan" in job_row(jid).error


def test_many_scripts_run_in_parallel_within_the_organizations_cap(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    # make five cases
    from tests.test_tcg import fake_converse, gen_url, run_next as run_tcg, two_cases

    for _ in range(2):
        fake_converse(monkeypatch, [two_cases()])
        admin.post(gen_url(pid, req["id"]))
        run_tcg()
    all_ids = [c["id"] for c in admin.get(f"/api/projects/{pid}/test-cases", params={"page_size": 100}).json()["test_cases"]][:5]
    r = start(admin, pid, *all_ids)
    assert len(r.json()["jobs"]) == 5
    claimed = []
    with new_session() as s:
        while (jid := jobs.claim(s, "w", ["generate_test_script"])) is not None:
            claimed.append(jid)
    assert len(claimed) == 3  # JOB_TENANT_CONCURRENCY: the other two wait their turn


def test_list_search_sort_and_paging(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    fake_script_converse(monkeypatch, [BODY])
    start(admin, pid, ids["TC-001"], ids["TC-002"])
    run_script_job()
    run_script_job()
    base = f"/api/projects/{pid}/test-scripts"
    assert admin.get(base, params={"q": "without a password"}).json()["total"] == 1
    assert admin.get(base, params={"q": "findElement"}).json()["total"] == 2
    assert [s["key"] for s in admin.get(base, params={"sort": "key", "direction": "desc"}).json()["scripts"]] == ["TS-002", "TS-001"]
    assert len(admin.get(base, params={"page_size": 1, "page": 2}).json()["scripts"]) == 1
    assert admin.get(base).json()["scripts"][0]["language"] == "javascript"


def test_other_organizations_see_nothing_and_change_nothing(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    fake_script_converse(monkeypatch, [BODY])
    start(admin, pid, ids["TC-001"])
    run_script_job()
    script_id = admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]["id"]
    other = new_client()
    assert signup_org(other, org="Other Inc", email="boss@other.example").status_code == 201
    opid = other.post("/api/projects", json={"name": "Theirs"}).json()["id"]
    for url in (f"/api/projects/{pid}/test-scripts", f"/api/projects/{pid}/test-scripts/{script_id}", f"/api/projects/{pid}/test-scripts/{script_id}/download"):
        assert other.get(url).status_code == 404, url
    assert other.post(f"/api/projects/{pid}/test-scripts/generate", json={"test_case_ids": [ids["TC-002"]]}).status_code == 404
    assert other.post(f"/api/projects/{opid}/test-scripts/generate", json={"test_case_ids": [ids["TC-002"]]}).status_code == 404
    assert other.get(f"/api/projects/{opid}/test-scripts/{script_id}").status_code == 404
    assert other.get(f"/api/projects/{opid}/test-scripts").json()["total"] == 0
    assert new_client().get(f"/api/projects/{pid}/test-scripts").status_code == 401
    assert admin.get(f"/api/projects/{pid}/test-scripts/{script_id}").status_code == 200


def test_missing_node_is_reported_not_hidden(monkeypatch):
    import subprocess

    def no_node(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", no_node)
    assert "Node.js was not found" in ts.check_syntax("const a = 1;")


def test_checks_that_can_never_pass_on_the_real_page_are_rejected():
    """Found live (TS-003, TS-011, TS-013): the same three patterns, each a script that fails by construction."""
    # a bare generic word also matches 'error_user' printed on the login page
    assert ts.extra_issues("await expectNoText(page, 'error');")
    assert ts.extra_issues("await expectNoText(page, ['Invalid', 'failed']);")
    assert not ts.extra_issues("await expectNoText(page, 'Epic sadface');")
    # a value the script typed is often printed on the page as a hint (sample login)
    typed = "await (await el('pw')).fill('secret_sauce');\nawait expectNoText(page, 'secret_sauce');"
    assert ts.extra_issues(typed)
    assert not ts.extra_issues("await (await el('pw')).fill('secret_sauce');\nawait expectNoText(page, 'Shipping Methods');")
    assert not ts.extra_issues("await expectNoText(page, 'secret_sauce');")  # nothing was typed, so no conflict
    # el() fails for a control that is not on the page, so it must not be the only use of an id
    assert ts.extra_issues("await expect(await el('backpack-add')).toBeHidden();")
    assert ts.extra_issues("await expect(await el('x')).not.toBeVisible();")
    assert ts.extra_issues("await expect(await el('x')).toHaveCount(0);")
    assert not ts.extra_issues("await (await el('modal-close')).click();\nawait expect(await el('modal-close')).toBeHidden();")
    assert not ts.extra_issues("await expect(await el('x')).toBeVisible();")
    assert not ts.extra_issues("await expect(await el('x')).toHaveCount(1);")
    # one message per distinct problem
    assert len(ts.extra_issues("await expectNoText(page, 'error');\nawait expectNoText(page, 'error');")) == 1


def test_an_empty_phrase_in_expecttext_is_rejected_and_sent_back(monkeypatch, db):
    """Found live: expectText(target, '') can never match (the helper drops empty phrases), so the step always failed."""
    assert ts.extra_issues("await expectText(await el('x'), '');")
    assert ts.extra_issues('await expectNoText(page, "");')
    assert not ts.extra_issues("await expectText(page, 'Products');") and not ts.extra_issues("await expectValue(await el('x'), '');")
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    calls = []
    bad = BODY.replace("await expectText(page, 'Products');", "await expectText(await el('login-btn'), '');")
    fake_script_converse(monkeypatch, [bad, BODY], calls)
    start(admin, pid, ids["TC-001"])
    jid, state = run_script_job()
    assert state == "succeeded" and "non-empty phrase" in calls[1]["messages"][-1]["content"]
