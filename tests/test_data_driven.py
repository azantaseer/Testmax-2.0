"""Data-driven testing: keys, data variations, scripts that read their values from a data block, running a variation."""
import json

import pytest
from sqlalchemy import select

from app.engines import datadriven as dd
from app.models_tests import TsRun, TsScript
from app.models_work import Job
from tests.conftest import new_client, signup_org
from tests.test_execute import online_runner, result
from tests.test_tcg import URL
from tests.test_tsg import fake_script_converse, run_script_job, seed_cases, start

STEPS = [
    {"stepNo": 1, "testData": URL, "fieldName": "—", "testStep": "Navigate to the application URL."},
    {"stepNo": 2, "testData": "standard_user", "fieldName": "Username", "testStep": 'Enter the "Username" field.'},
    {"stepNo": 3, "testData": "secret_sauce", "fieldName": "Password", "testStep": 'Enter the "Password" field.'},
    {"stepNo": 4, "testData": "—", "fieldName": "Login", "testStep": 'Click the "Login" button.'},
]

BODY = f"""await test.step('Step 1: Navigate to the application URL.', async () => {{
  await page.goto('{URL}');
}});
await test.step('Step 2: Enter the "Username" field.', async () => {{
  await (await el('login-username')).fill('standard_user');
}});
await test.step('Step 3: Click the "Login" button.', async () => {{
  await (await el('login-btn')).click();
  await expectText(page, 'Products');
}});"""


# ---- pure rules ------------------------------------------------------------
def test_keys_are_derived_once_and_kept():
    keyed = dd.with_keys(STEPS)
    assert [s.get("dataKey") for s in keyed] == ["destination_url", "username", "password", None]
    assert dd.with_keys(keyed) == keyed  # stable
    renamed = [dict(s) for s in keyed]
    renamed[1]["fieldName"] = "User name"
    assert dd.with_keys(renamed)[1]["dataKey"] == "username"  # a key never changes because a label was edited
    twin = dd.with_keys([{"stepNo": 1, "testData": "a", "fieldName": "Name"}, {"stepNo": 2, "testData": "b", "fieldName": "Name"}])
    assert [s["dataKey"] for s in twin] == ["name", "name_2"]
    assert dd.keyed_steps([{"stepNo": 1, "testData": "<authorized password>", "fieldName": "Password"}, {"stepNo": 2, "testData": "(empty)", "fieldName": "x"}]) == []


def test_values_fall_back_to_the_default_and_empty_means_empty():
    steps = dd.with_keys(STEPS)
    assert dd.values_for(steps, None) == {"destination_url": URL, "username": "standard_user", "password": "secret_sauce"}
    values = dd.values_for(steps, {"values": {"username": "locked_out_user", "password": "", "nope": "x"}})
    assert values["username"] == "locked_out_user" and values["password"] == "secret_sauce" and "nope" not in values
    assert dd.values_for(steps, {"values": {"username": "(empty)"}})["username"] == ""


def test_parameterize_replaces_only_the_value_of_its_own_step():
    keyed = dd.with_keys(STEPS)
    code = (
        "const { test, expect } = require('@playwright/test');\n\n"
        "test('t', async ({ page }) => {\n"
        "  await test.step('Step 1: Navigate to the application URL.', async () => {\n"
        f"    await page.goto('{URL}');\n"
        "  });\n\n"
        "  await test.step('Step 2: Enter the \"Username\" field.', async () => {\n"
        "    await (await el('u')).fill('standard_user');\n"
        "    await expectText(page, 'standard_user is not a heading');\n"
        "  });\n\n"
        "  await test.step('Step 3: Enter the \"Password\" field.', async () => {\n"
        "    await (await el('p')).fill('something the AI changed');\n"
        "  });\n\n"
        "  await test.step('Step 4: Click the \"Login\" button.', async () => {\n"
        "    await (await el('b')).click();\n"
        "    await expectText(page, 'standard_user');\n"  # the same text in another step is not touched
        "  });\n"
        "});\n"
    )
    out = dd.parameterize(code, keyed)
    assert out["keys"] == ["destination_url", "username"]
    assert out["gaps"] == [{"stepNo": 3, "key": "password"}]  # the value was not found as typed: reported, not guessed
    assert 'page.goto(DATA["destination_url"])' in out["code"] and 'fill(DATA["username"])' in out["code"]
    assert "something the AI changed" in out["code"] and "expectText(page, 'standard_user');" in out["code"]
    assert "'standard_user is not a heading'" in out["code"]  # only an exact value is replaced
    assert dd.is_data_driven(out["code"]) and dd.block_keys(out["code"]) == ["destination_url", "username"]
    assert dd.parameterize(out["code"], keyed)["code"] == out["code"]  # nothing happens twice
    filled = dd.rewrite_block(out["code"], {"username": "locked_out_user", "destination_url": "https://x.test/", "password": "unused"})
    assert '"username": "locked_out_user"' in filled and "password" not in filled.split("TESTMAX-DATA-END")[0]


def test_data_sets_are_validated():
    keys = ["username", "password"]
    ok = dd.clean_data_sets([{"name": "  Locked   User ", "values": {"username": "a\x00b", "password": ""}}], keys)
    assert ok[0]["name"] == "Locked User" and ok[0]["values"]["username"] == "ab" and ok[0]["id"]
    with pytest.raises(Exception, match="not a test data key"):
        dd.clean_data_sets([{"name": "x", "values": {"nope": "1"}}], keys)
    with pytest.raises(Exception, match="At most"):
        dd.clean_data_sets([{}] * 21, keys)
    with pytest.raises(Exception, match="longer"):
        dd.clean_data_sets([{"values": {"username": "x" * 2001}}], keys)


def test_secret_looking_values_are_hidden_in_the_report():
    assert dd.mask_values({"username": "a", "password": "p", "api_key": "k", "token_2": ""}) == {"username": "a", "password": "•" * 6, "api_key": "•" * 6, "token_2": ""}


# ---- through the API -------------------------------------------------------
def make(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    calls = []
    fake_script_converse(monkeypatch, [BODY], calls)
    assert start(admin, pid, ids["TC-001"]).status_code == 202
    run_script_job()
    script = admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]
    return admin, pid, script, ids, calls


def case_url(pid, cid):
    return f"/api/projects/{pid}/test-cases/{cid}"


def test_a_generated_script_reads_its_values_from_a_data_block(monkeypatch, db):
    admin, pid, script, ids, calls = make(monkeypatch, db)
    assert script["data_driven"] is True
    one = admin.get(f"/api/projects/{pid}/test-scripts/{script['id']}").json()
    assert one["data_keys"] == ["destination_url", "username"] and one["data_gaps"] == []
    assert 'fill(DATA["username"])' in one["code"] and '"username": "standard_user"' in one["code"]
    assert "standard_user" not in one["code"].split("TESTMAX-DATA-END")[1]  # the value is only in the data block now
    case = admin.get(case_url(pid, ids["TC-001"])).json()
    assert [s.get("dataKey") for s in case["steps"]] == ["destination_url", "username", None]
    assert case["data"]["default"] == {"destination_url": URL, "username": "standard_user"} and case["data"]["sets"] == []
    assert [k["key"] for k in case["data"]["keys"]] == ["destination_url", "username"]
    # the AI is given the steps exactly as before: no keys
    assert "dataKey" not in json.dumps(calls[0]["messages"])


def test_data_variations_are_saved_and_never_make_the_script_out_of_date(monkeypatch, db):
    admin, pid, script, ids, _ = make(monkeypatch, db)
    cu = case_url(pid, ids["TC-001"])
    saved = admin.put(f"{cu}/data-sets", json={"data_sets": [{"name": "Locked User", "values": {"username": "locked_out_user"}}, {"name": "", "values": {}}]})
    assert saved.status_code == 200
    sets = saved.json()["sets"]
    assert [s["name"] for s in sets] == ["Locked User", ""] and sets[0]["values"] == {"username": "locked_out_user"}
    assert admin.get(cu).json()["version"] == 1  # variations are not a change of the test case
    assert admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][0]["script"]["stale"] is False
    bad = admin.put(f"{cu}/data-sets", json={"data_sets": [{"name": "x", "values": {"password": "nope"}}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_data_sets"
    assert admin.put(f"{cu}/data-sets", json={"data_sets": []}).json()["sets"] == []
    other = new_client()  # another organization cannot touch it
    assert signup_org(other, org="Other Co", email="boss@other.example").status_code == 201
    assert other.put(f"{cu}/data-sets", json={"data_sets": []}).status_code == 404


def test_changing_only_the_test_data_keeps_the_script_current_but_other_edits_do_not(monkeypatch, db):
    admin, pid, script, ids, _ = make(monkeypatch, db)
    cu = case_url(pid, ids["TC-001"])
    case = admin.get(cu).json()
    steps = [{k: s[k] for k in ("testStep", "fieldName", "testData", "expectedResult", "sourceElementId", "dataKey") if k in s} for s in case["steps"]]
    steps[1]["testData"] = "problem_user"
    assert admin.patch(cu, json={"version": case["version"], "steps": steps}).status_code == 200
    row = admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][0]
    assert row["version"] == 2 and row["script"]["stale"] is False  # data only: the script is still right
    assert '"username": "problem_user"' in admin.get(f"/api/projects/{pid}/test-scripts/{script['id']}").json()["code"]
    steps[1]["testStep"] = "Type the user name"  # a real change of the steps
    assert admin.patch(cu, json={"version": 2, "steps": steps}).status_code == 200
    assert admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][0]["script"]["stale"] is True


def test_execute_runs_each_chosen_variation_with_its_values(monkeypatch, db):
    admin, pid, script, ids, _ = make(monkeypatch, db)
    cu = case_url(pid, ids["TC-001"])
    sets = admin.put(f"{cu}/data-sets", json={"data_sets": [{"name": "Locked User", "values": {"username": "locked_out_user"}}]}).json()["sets"]
    runner, _ = online_runner(admin)
    ex = f"/api/projects/{pid}/test-scripts/{script['id']}/execute"
    assert admin.post(ex, json={"data_sets": ["nope"]}).status_code == 404
    r = admin.post(ex, json={"data_sets": ["default", sets[0]["id"]]})
    assert r.status_code == 202 and len(r.json()["jobs"]) == 2
    jobs = db.scalars(select(Job).where(Job.type == "run_script").order_by(Job.created_at)).all()
    assert [j.payload["data_set"]["name"] for j in jobs] == ["DV-1 (Default)", "DV-2 — Locked User"]
    assert '"username": "standard_user"' in jobs[0].payload["code"] and '"username": "locked_out_user"' in jobs[1].payload["code"]
    claimed = runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert claimed and claimed["payload"]["data_set"]["name"] in ("DV-1 (Default)", "DV-2 — Locked User")
    assert runner.post(f"/api/runner/jobs/{claimed['id']}/complete", json={"result": result("passed")}).status_code == 200
    run = db.scalars(select(TsRun)).first()
    report = admin.get(f"/api/projects/{pid}/test-runs/{run.id}").json()
    assert report["data_set"]["name"] == claimed["payload"]["data_set"]["name"] and "username" in report["data_set"]["values"]


def test_the_test_case_is_read_when_execute_is_clicked(monkeypatch, db):
    admin, pid, script, ids, _ = make(monkeypatch, db)
    cu = case_url(pid, ids["TC-001"])
    case = admin.get(cu).json()
    steps = [{k: s[k] for k in ("testStep", "fieldName", "testData", "expectedResult", "sourceElementId", "dataKey") if k in s} for s in case["steps"]]
    steps[1]["testData"] = "performance_glitch_user"
    admin.patch(cu, json={"version": case["version"], "steps": steps})
    online_runner(admin)
    assert admin.post(f"/api/projects/{pid}/test-scripts/{script['id']}/execute").status_code == 202
    job = db.scalars(select(Job).where(Job.type == "run_script")).first()
    assert '"username": "performance_glitch_user"' in job.payload["code"]


def test_download_fills_in_the_chosen_variation(monkeypatch, db):
    admin, pid, script, ids, _ = make(monkeypatch, db)
    cu = case_url(pid, ids["TC-001"])
    sets = admin.put(f"{cu}/data-sets", json={"data_sets": [{"name": "B", "values": {"username": "visual_user"}}]}).json()["sets"]
    d = f"/api/projects/{pid}/test-scripts/{script['id']}/download"
    assert '"username": "standard_user"' in admin.get(d).text
    assert '"username": "visual_user"' in admin.get(d, params={"data_set": sets[0]["id"]}).text
    assert admin.get(d, params={"data_set": "gone"}).status_code == 404


def test_an_older_script_can_be_made_data_driven_without_the_ai(monkeypatch, db):
    admin, pid, script, ids, _ = make(monkeypatch, db)
    row = db.scalar(select(TsScript))
    plain = row.code.replace(dd.render_block({"destination_url": URL, "username": "standard_user"}), "").replace('DATA["destination_url"]', f"'{URL}'").replace('DATA["username"]', "'standard_user'")
    row.code, row.data_keys = plain, []
    db.commit()
    u = f"/api/projects/{pid}/test-scripts/{script['id']}"
    online_runner(admin)
    refused = admin.post(f"{u}/execute", json={"data_sets": [admin_set(admin, pid, ids)]})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "not_data_driven"
    done = admin.post(f"{u}/make-data-driven")
    assert done.status_code == 200 and done.json()["data_keys"] == ["destination_url", "username"]
    assert admin.post(f"{u}/make-data-driven").json()["error"]["code"] == "already_data_driven"
    assert 'fill(DATA["username"])' in admin.get(u).json()["code"]


def admin_set(admin, pid, ids):
    return admin.put(case_url(pid, ids["TC-001"]) + "/data-sets", json={"data_sets": [{"name": "B", "values": {"username": "x"}}]}).json()["sets"][0]["id"]
