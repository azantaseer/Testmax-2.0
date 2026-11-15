"""Editing requirements, test cases and steps by hand: validation, conflicts, stale scripts, isolation."""
import io

from docx import Document as DocxDocument

from tests.conftest import new_client, signup_org
from tests.test_tsg import BODY, fake_script_converse, run_script_job, seed_cases, start


def case_url(pid, case_id):
    return f"/api/projects/{pid}/test-cases/{case_id}"


def step(text, field="", data="", expected="", source=None):
    out = {"testStep": text, "fieldName": field, "testData": data, "expectedResult": expected}
    if source:
        out["sourceElementId"] = source
    return out


def get_case(admin, pid, case_id):
    return admin.get(case_url(pid, case_id)).json()


# ---- test cases --------------------------------------------------------------------------
def test_edit_the_description_fields_of_a_test_case(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    before = get_case(admin, pid, ids["TC-001"])
    assert before["version"] == 1 and before["edited"] is False
    r = admin.patch(case_url(pid, ids["TC-001"]), json={
        "version": 1, "title": "  Verify   a standard user can sign in ", "type": "edge", "overview": "New overview.\r\nSecond line",
        "prerequisites": ["  An account exists. ", "", "The shop is online."], "objective": "Check sign in.", "expected_result": "The shop page opens.",
    })
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["title"], c["type"], c["overview"]) == ("Verify a standard user can sign in", "EDGE", "New overview.\nSecond line")
    assert c["prerequisites"] == ["An account exists.", "The shop is online."] and c["objective"] == "Check sign in." and c["expected_result"] == "The shop page opens."
    assert c["version"] == 2 and c["edited"] is True and c["updated_at"] >= before["updated_at"]
    assert c["steps"] == before["steps"] and c["warnings"] == []  # steps were not part of this edit
    listed = admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][0]
    assert listed["title"] == "Verify a standard user can sign in" and listed["type"] == "EDGE" and listed["edited"] is True


def test_edit_the_steps_renumbers_keeps_known_sources_and_warns(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    before = get_case(admin, pid, ids["TC-001"])
    sources = [s.get("sourceElementId") for s in before["steps"]]
    assert sources[1] == "login-username" and sources[2] == "login-btn"
    steps = [
        step("Click the Login button", "Login", "", "The page opens.", source="login-btn"),         # moved up, keeps its source
        step("Navigate to the application URL", "", "https://shop.example/", "", source="made-up"),  # a source the case never had is dropped
        step("Look at the screen"),                                                                  # not an approved verb: warning
        step("Enter the username and click Login", "Username", "standard_user"),                     # two actions: warning
    ]
    r = admin.patch(case_url(pid, ids["TC-001"]), json={"version": 1, "steps": steps})
    assert r.status_code == 200, r.text
    got = r.json()["steps"]
    assert [s["stepNo"] for s in got] == [1, 2, 3, 4]
    assert got[0] == {"stepNo": 1, "testStep": "Click the Login button", "fieldName": "Login", "testData": "—", "expectedResult": "The page opens.", "sourceElementId": "login-btn"}
    assert got[1]["fieldName"] == "—" and got[1]["testData"] == "https://shop.example/" and got[1]["expectedResult"] == "—" and got[1]["sourceElementId"] is None
    w = " ".join(r.json()["warnings"])
    assert "Step 3 does not start with an approved action" in w and "Step 4 seems to combine more than one action" in w and "Step 1" not in w


def test_invalid_edits_are_refused_and_change_nothing(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    url = case_url(pid, ids["TC-001"])
    before = get_case(admin, pid, ids["TC-001"])
    bad = [
        {"title": "   "}, {"type": "FUNNY"}, {"expected_result": " "}, {"steps": []}, {"steps": [step("  ")]},
        {"title": "x" * 301}, {"steps": [step("Click the button")] * 101}, {"steps": [step("x" * 1001)]}, {"prerequisites": ["y" * 501]},
        {"steps": [{"fieldName": "no step text"}]},
    ]
    for patch in bad:
        r = admin.patch(url, json={"version": 1, **patch})
        assert r.status_code == 422, patch
    assert admin.patch(url, json={"title": "no version"}).status_code == 422
    after = get_case(admin, pid, ids["TC-001"])
    assert after["version"] == 1 and after["title"] == before["title"] and after["steps"] == before["steps"] and after["edited"] is False


def test_text_is_cleaned_of_control_characters(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    r = admin.patch(case_url(pid, ids["TC-001"]), json={"version": 1, "title": "A\x00B\x07C", "overview": "<script>alert(1)</script>"})
    assert r.json()["title"] == "ABC" and r.json()["overview"] == "<script>alert(1)</script>"  # stored as text; the page shows it as text, never as HTML


def test_an_edit_that_changes_nothing_does_not_bump_the_version(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    c = get_case(admin, pid, ids["TC-001"])
    r = admin.patch(case_url(pid, ids["TC-001"]), json={"version": 1, "title": c["title"], "steps": [step(s["testStep"], s["fieldName"], s["testData"], s["expectedResult"], s.get("sourceElementId")) for s in c["steps"]]})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["edited"] is False


def test_two_people_editing_at_once_cannot_overwrite_each_other(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    url = case_url(pid, ids["TC-001"])
    assert admin.patch(url, json={"version": 1, "title": "First save"}).status_code == 200
    late = admin.patch(url, json={"version": 1, "title": "Second save, based on the old page"})
    assert late.status_code == 409 and late.json()["error"]["code"] == "edit_conflict" and "Reload" in late.json()["error"]["message"]
    assert get_case(admin, pid, ids["TC-001"])["title"] == "First save"
    assert admin.patch(url, json={"version": 2, "title": "Third save"}).json()["version"] == 3


def test_the_script_is_reported_out_of_date_until_it_is_regenerated(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    calls = []
    fake_script_converse(monkeypatch, [BODY], calls)
    start(admin, pid, ids["TC-001"])
    run_script_job()
    url = case_url(pid, ids["TC-001"])
    sid = admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]["id"]
    assert get_case(admin, pid, ids["TC-001"])["script"]["stale"] is False
    admin.patch(url, json={"version": 1, "title": "Renamed by hand"})
    assert get_case(admin, pid, ids["TC-001"])["script"]["stale"] is True
    assert admin.get(f"/api/projects/{pid}/test-scripts/{sid}").json()["stale"] is True
    assert admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]["stale"] is True
    assert admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][0]["script"]["stale"] is True
    # regenerating writes the script from the edited case and clears the flag
    start(admin, pid, ids["TC-001"])
    run_script_job()
    assert "Renamed by hand" in calls[-1]["messages"][0]["content"]
    assert get_case(admin, pid, ids["TC-001"])["script"]["stale"] is False
    assert admin.get(f"/api/projects/{pid}/test-scripts/{sid}").json()["stale"] is False
    # the other case never had a script, so nothing is stale there
    assert get_case(admin, pid, ids["TC-002"])["script"] is None


def test_the_word_file_shows_the_edited_test_case(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    admin.patch(case_url(pid, ids["TC-001"]), json={"version": 1, "title": "Edited title for the Word file", "steps": [step("Navigate to the application URL", "", "https://shop.example/", "The login page is shown.")]})
    set_id = admin.get(f"/api/projects/{pid}/test-case-sets").json()["sets"][0]["id"]
    doc = DocxDocument(io.BytesIO(admin.get(f"/api/projects/{pid}/test-case-sets/{set_id}/docx").content))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
    assert "Edited title for the Word file" in text and "The login page is shown." in text


# ---- requirements ------------------------------------------------------------------------
def test_edit_a_requirement(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    url = f"/api/projects/{pid}/requirements/{req['id']}"
    before = admin.get(url).json()
    assert before["version"] == 1 and before["edited"] is False
    r = admin.patch(url, json={"version": 1, "summary": "  Sign in   with a valid user ", "description": "## Overview\r\nA user signs in.", "acceptance_criteria": "- It works."})
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["summary"], got["description"], got["acceptance_criteria"]) == ("Sign in with a valid user", "## Overview\nA user signs in.", "- It works.")
    assert got["version"] == 2 and got["edited"] is True and got["scan"] is not None  # scans and test cases are left alone
    assert admin.get(url).json()["summary"] == "Sign in with a valid user"
    assert admin.get(f"/api/projects/{pid}/requirements").json()["requirements"][0]["summary"] == "Sign in with a valid user"
    only = admin.patch(url, json={"version": 2, "acceptance_criteria": ""})  # acceptance criteria may be emptied; the other fields stay
    assert only.status_code == 200 and only.json()["acceptance_criteria"] == "" and only.json()["summary"] == "Sign in with a valid user"


def test_requirement_edit_validation_and_conflict(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    url = f"/api/projects/{pid}/requirements/{req['id']}"
    for patch in ({"summary": "  "}, {"description": ""}, {"summary": "x" * 301}, {"description": "y" * 60001}):
        assert admin.patch(url, json={"version": 1, **patch}).status_code == 422, patch
    assert admin.patch(url, json={"summary": "no version"}).status_code == 422
    assert admin.patch(url, json={"version": 1, "summary": "A"}).status_code == 200
    late = admin.patch(url, json={"version": 1, "summary": "B"})
    assert late.status_code == 409 and late.json()["error"]["code"] == "edit_conflict"
    assert admin.get(url).json()["summary"] == "A"


# ---- isolation ---------------------------------------------------------------------------
def test_other_organizations_and_projects_cannot_edit(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    other = new_client()
    assert signup_org(other, org="Other Inc", email="boss@other.example").status_code == 201
    other_pid = other.post("/api/projects", json={"name": "Theirs"}).json()["id"]
    case = {"version": 1, "title": "Hacked"}
    # another organization: not their project, and not their ids inside their own project
    assert other.patch(case_url(pid, ids["TC-001"]), json=case).status_code == 404
    assert other.patch(f"/api/projects/{pid}/requirements/{req['id']}", json={"version": 1, "summary": "Hacked"}).status_code == 404
    assert other.patch(case_url(other_pid, ids["TC-001"]), json=case).status_code == 404
    assert other.patch(f"/api/projects/{other_pid}/requirements/{req['id']}", json={"version": 1, "summary": "Hacked"}).status_code == 404
    # the same organization, another project
    second = admin.post("/api/projects", json={"name": "Second"}).json()["id"]
    assert admin.patch(case_url(second, ids["TC-001"]), json=case).status_code == 404
    assert admin.patch(f"/api/projects/{second}/requirements/{req['id']}", json={"version": 1, "summary": "Hacked"}).status_code == 404
    assert new_client().patch(case_url(pid, ids["TC-001"]), json=case).status_code == 401
    assert get_case(admin, pid, ids["TC-001"])["title"] != "Hacked" and admin.get(f"/api/projects/{pid}/requirements/{req['id']}").json()["summary"] != "Hacked"
