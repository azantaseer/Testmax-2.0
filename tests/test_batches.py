"""Test batches: scripts joined into one Playwright test (one browser session), batch CRUD, running a batch, one result per script."""
import json
import re
import shutil
import subprocess

import pytest
from sqlalchemy import select

from app.engines import batch as be
from app.engines import tsg
from app.engines.datadriven import BLOCK_PATTERN as BLOCK
from app.models_tests import TbRun, TsRun, TsRunArtifact, TsScript
from app.models_work import Job
from tests.conftest import new_client, signup_org
from tests.test_execute import PNG, claim_and_complete, online_runner, result
from tests.test_tsg import BODY, fake_script_converse, run_script_job, seed_cases, start

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed")

ELEMENTS = {"login-btn": {"name": "Login", "candidates": ["page.getByRole('button', { name: 'Login' })"]}}


def script(key, body):
    return tsg.assemble_script({"id": key, "title": f"Title {key}", "testCaseType": "POSITIVE"}, "SET-1", ELEMENTS, body)


def mini(step, work):
    return f"await test.step('Step 1: {step}', async () => {{\n  {work}\n}});"


# ---- the joined script ---------------------------------------------------------------------------
def test_split_script_finds_the_preamble_and_the_body():
    pre, body = be.split_script(script("TC-001", mini("Open", "page.trail.push('A');")))
    assert any("const ELEMENTS" in line for line in pre) and not any("require(" in line for line in pre)
    assert body[0].startswith("  const el = ") and body[-1] == "  });"
    for bad in ("", "const x = 1;", "const { test, expect } = require('@playwright/test');\ntest('a', async ({ page }) => {\n});\ntest('b', async ({ page }) => {\n});\n"):
        with pytest.raises(ValueError):
            be.split_script(bad)


@needs_node
def test_the_joined_script_is_valid_javascript_and_each_script_keeps_its_own_scope():
    joined = be.build_script("Register then search", "stop", [{"key": "TS-001", "code": script("TC-001", mini("A", "page.trail.push('A');"))}, {"key": "TS-002", "code": script("TC-002", mini("B", "page.trail.push('B');"))}])
    assert tsg.check_syntax(joined) is None
    assert joined.count("const ELEMENTS") == 2 and joined.count("require('@playwright/test')") == 1  # their own helpers, one import
    with pytest.raises(ValueError, match="TS-009"):
        be.build_script("x", "stop", [{"key": "TS-009", "code": "nonsense"}])


STUB = """
const log = [];
function test(title, fn) { test.fn = fn; test.title = title; }
test.step = async (title, fn) => { const rec = { title }; log.push(rec); try { await fn(); } catch (e) { rec.error = e.message; throw e; } };
test.setTimeout = (ms) => { log.push({ timeout: ms }); };
test.info = () => ({ outputPath: (n) => n, attach: async (name) => { log.push({ attach: name }); } });
module.exports = { test, expect: () => ({ ok: true }), log };
"""
RUNNER = """
const { test, log } = require('@playwright/test');
require('./batch.spec.js');
(async () => {
  const page = { trail: [], screenshot: async () => {}, on: () => {}, mouse: { move: async () => {} } };
  let error = null;
  try { await test.fn({ page }); } catch (e) { error = e.message; }
  console.log(JSON.stringify({ log, error, trail: page.trail }));
})();
"""


def run_in_node(tmp_path, joined):
    stub = tmp_path / "node_modules" / "@playwright" / "test"
    stub.mkdir(parents=True)
    (stub / "index.js").write_text(STUB)
    (tmp_path / "batch.spec.js").write_text(joined)
    (tmp_path / "run.js").write_text(RUNNER)
    out = subprocess.run(["node", "run.js"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def titles(report):
    return [e["title"] for e in report["log"] if "title" in e]


@needs_node
def test_stop_mode_ends_at_the_first_failed_script_and_continue_mode_goes_on(tmp_path):
    ok = script("TC-001", mini("Register", "page.trail.push('registered');"))
    uses_state = script("TC-003", mini("Search", "if (!page.trail.includes('registered')) throw new Error('not signed in'); page.trail.push('searched');"))
    broken = script("TC-002", mini("Breaks", "throw new Error('boom');"))
    items = [{"key": "TS-001", "code": ok}, {"key": "TS-002", "code": broken}, {"key": "TS-003", "code": uses_state}]
    (tmp_path / "a").mkdir()
    stopped = run_in_node(tmp_path / "a", be.build_script("b", "stop", items))
    assert [be.parse_marker(t)[:2] for t in titles(stopped) if be.parse_marker(t)] == [("START", 1), ("DONE", 1), ("START", 2), ("FAIL", 2)]
    assert stopped["error"] == "boom" and stopped["trail"] == ["registered"]  # the third script never started
    (tmp_path / "b").mkdir()
    went_on = run_in_node(tmp_path / "b", be.build_script("b", "continue", items))
    assert [be.parse_marker(t)[:2] for t in titles(went_on) if be.parse_marker(t)] == [("START", 1), ("DONE", 1), ("START", 2), ("FAIL", 2), ("START", 3), ("DONE", 3)]
    assert went_on["trail"] == ["registered", "searched"]  # the later script still found the state of the first one: one page
    assert went_on["error"] == "1 of 3 scripts failed: TS-002"
    assert sum(1 for e in went_on["log"] if "attach" in e) == 3  # a screenshot after every script that ran
    assert next(e for e in went_on["log"] if e.get("title", "").startswith("Step 0 TMX-FAIL 2")).get("error") == "boom"


def test_the_runners_step_list_is_cut_into_one_part_per_script():
    steps = [
        {"title": "Step 0 TMX-START 1 TS-001", "status": "passed"},
        {"title": "Step 1: Open", "status": "passed"},
        {"title": "Step 0 TMX-DONE 1 2500", "status": "passed"},
        {"title": "Step 0 TMX-START 2 TS-002", "status": "passed"},
        {"title": "Step 1: Click", "status": "failed", "error": "no such element"},
        {"title": "Step 0 TMX-FAIL 2 900", "status": "failed", "error": "no such element"},
    ]
    parts = be.split_steps(steps, 3)
    assert [p["started"] for p in parts.values()] == [True, True, False]
    assert parts[1]["done"] and parts[1]["duration_ms"] == 2500 and [s["title"] for s in parts[1]["steps"]] == ["Step 1: Open"]
    assert parts[2]["failed"] and parts[2]["error"] == "no such element" and parts[2]["duration_ms"] == 900
    assert be.parse_marker("Step 1: Open") is None


# ---- the API --------------------------------------------------------------------------------------
def make_two(monkeypatch, db):
    """TS-001 (data-driven, with a second variation) and TS-002 (not data-driven: its steps are different)."""
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    fake_script_converse(monkeypatch, [BODY])
    assert start(admin, pid, ids["TC-001"], ids["TC-002"]).status_code == 202
    run_script_job()
    run_script_job()
    scripts = {s["test_case_key"]: s for s in admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"]}
    # make TS-002 a script from before data variations existed: its values are typed into the script, it has no data block
    old = db.get(TsScript, scripts["TC-002"]["id"])
    code = BLOCK.sub("", old.code)
    for key, value in json.loads("{" + ", ".join(line.strip().rstrip(",") for line in re.findall(r'^  "[^"]+": ".*",?$', old.code, re.M)) + "}").items():
        code = code.replace(f'DATA["{key}"]', json.dumps(value))
    old.code, old.data_keys = code, []
    db.commit()
    scripts["TC-002"]["data_driven"] = False
    return admin, pid, scripts, ids


def add_variation(admin, pid, ids):
    cu = f"/api/projects/{pid}/test-cases/{ids['TC-001']}"
    sets = admin.put(f"{cu}/data-sets", json={"data_sets": [{"name": "Visual User", "values": {"username": "visual_user"}}]}).json()["sets"]
    return sets[0]["id"]


def url(pid, tail=""):
    return f"/api/projects/{pid}/test-batches{tail}"


def body(scripts, *picks, **extra):
    return {"name": "Register then search", "on_failure": "stop", "items": [{"script_id": scripts[k]["id"], "data_set_id": d} for k, d in picks], **extra}


def test_create_read_edit_and_delete_a_batch(monkeypatch, db):
    admin, pid, scripts, ids = make_two(monkeypatch, db)
    dv2 = add_variation(admin, pid, ids)
    made = admin.post(url(pid), json=body(scripts, ("TC-001", "default"), ("TC-002", "default"), ("TC-001", dv2), description="  Sign up, then look something up ")).json()
    assert made["key"] == "TB-001" and made["on_failure"] == "stop" and made["description"] == "Sign up, then look something up"
    assert [(i["script_key"], i["data_set_label"]) for i in made["items"]] == [("TS-001", "DV-1 (Default)"), ("TS-002", "DV-1 (Default)"), ("TS-001", "DV-2 — Visual User")]
    one = admin.get(url(pid, f"/{made['id']}")).json()
    assert one["item_count"] == 3 and one["max_items"] == 8 and one["recent_runs"] == [] and one["running_job"] is None
    first = one["items"][0]
    assert first["data_driven"] is True and [c["id"] for c in first["data_sets"]] == ["default", dv2]
    assert one["items"][1]["data_driven"] is False and [c["id"] for c in one["items"][1]["data_sets"]] == ["default"]  # only its own values
    listed = admin.get(url(pid)).json()
    assert listed["total"] == 1 and listed["batches"][0]["item_count"] == 3 and listed["batches"][0]["last_run"] is None
    assert admin.get(url(pid) + "?q=search").json()["total"] == 1 and admin.get(url(pid) + "?q=nothing").json()["total"] == 0
    # edit: new order, the other setting
    edited = admin.put(url(pid, f"/{made['id']}"), json=body(scripts, ("TC-002", "default"), ("TC-001", dv2), name="Renamed", on_failure="continue"))
    assert edited.status_code == 200
    got = edited.json()
    assert got["name"] == "Renamed" and got["on_failure"] == "continue" and [i["script_key"] for i in got["items"]] == ["TS-002", "TS-001"]
    assert admin.post(url(pid), json=body(scripts, ("TC-001", "default"))).json()["key"] == "TB-002"
    assert admin.delete(url(pid, f"/{made['id']}")).status_code == 204
    assert admin.get(url(pid, f"/{made['id']}")).status_code == 404


def test_what_a_batch_accepts(monkeypatch, db):
    admin, pid, scripts, ids = make_two(monkeypatch, db)
    dv2 = add_variation(admin, pid, ids)
    good = body(scripts, ("TC-001", "default"))
    assert admin.post(url(pid), json={**good, "name": "  "}).json()["error"]["message"] == "Enter a name for the batch."
    assert admin.post(url(pid), json={**good, "items": []}).json()["error"]["message"] == "Add at least one test script."
    assert "8 scripts at most" in admin.post(url(pid), json={**good, "items": good["items"] * 9}).json()["error"]["message"]
    assert admin.post(url(pid), json={**good, "items": good["items"] * 8}).status_code == 201  # exactly the limit; the same script may appear again
    assert admin.post(url(pid), json={**good, "on_failure": "maybe"}).status_code == 422
    err = admin.post(url(pid), json=body(scripts, ("TC-002", dv2))).json()["error"]  # a variation of another test case
    assert err["code"] == "not_data_driven" and "TS-002" in err["message"]
    err = admin.post(url(pid), json=body(scripts, ("TC-001", "nope"))).json()["error"]
    assert err["code"] == "data_set_not_found"
    assert admin.post(url(pid), json={**good, "items": [{"script_id": "00000000-0000-0000-0000-000000000000"}]}).status_code == 404
    # a script that is not in the shape TestMax generates cannot be joined
    db.get(TsScript, scripts["TC-002"]["id"]).code = "console.log('hand written');"
    db.commit()
    err = admin.post(url(pid), json=body(scripts, ("TC-002", "default"))).json()["error"]
    assert err["code"] == "script_not_batchable" and "TS-002" in err["message"]


def test_other_organizations_see_nothing_of_a_batch(monkeypatch, db):
    admin, pid, scripts, ids = make_two(monkeypatch, db)
    made = admin.post(url(pid), json=body(scripts, ("TC-001", "default"))).json()
    other = new_client()
    assert signup_org(other, org="Other Inc", email="boss@other.example").status_code == 201
    opid = other.post("/api/projects", json={"name": "Theirs"}).json()["id"]
    for project in (pid, opid):
        assert other.get(url(project, f"/{made['id']}")).status_code == 404
        assert other.put(url(project, f"/{made['id']}"), json=body(scripts, ("TC-001", "default"))).status_code == 404
        assert other.delete(url(project, f"/{made['id']}")).status_code == 404
        assert other.post(url(project, f"/{made['id']}/run")).status_code == 404
    assert other.get(url(pid)).status_code == 404 and new_client().get(url(pid)).status_code == 401
    assert other.post(url(opid), json=body(scripts, ("TC-001", "default"))).status_code == 404  # their project, our scripts


# ---- running --------------------------------------------------------------------------------------
def make_batch(admin, pid, scripts, ids, on_failure="stop"):
    dv2 = add_variation(admin, pid, ids)
    made = admin.post(url(pid), json=body(scripts, ("TC-001", "default"), ("TC-002", "default"), ("TC-001", dv2), on_failure=on_failure)).json()
    return made, dv2


def marker(kind, n, key="", ms=0):
    title = {"START": f"Step 0 TMX-START {n} {key}", "DONE": f"Step 0 TMX-DONE {n} {ms}", "FAIL": f"Step 0 TMX-FAIL {n} {ms}"}[kind]
    return {"title": title, "status": "failed" if kind == "FAIL" else "passed", "duration_ms": 0, "error": "element not found" if kind == "FAIL" else ""}


def step(name, status="passed", error=""):
    return {"title": name, "status": status, "duration_ms": 100, "error": error}


def start_run(admin, pid, batch_id, **kw):
    return admin.post(url(pid, f"/{batch_id}/run"), json=kw)


def test_running_a_batch_sends_one_joined_script_to_the_runner(monkeypatch, db):
    admin, pid, scripts, ids = make_two(monkeypatch, db)
    made, dv2 = make_batch(admin, pid, scripts, ids)
    assert start_run(admin, pid, made["id"]).json()["error"]["code"] == "no_runner_online"
    runner, _ = online_runner(admin)
    started = start_run(admin, pid, made["id"])
    assert started.status_code == 202
    answer = started.json()
    assert answer["batch_id"] == made["id"] and [i["label"] for i in answer["items"]] == ["DV-1 (Default)", None, "DV-2 — Visual User"]
    job = db.get(Job, answer["id"])
    code = job.payload["code"]
    assert job.type == "run_script" and job.max_attempts == 1 and job.payload["file_name"] == "TB-001.spec.js"
    assert code.count("function __tmx_script_") == 3 and code.count("require('@playwright/test')") == 1
    assert '"username": "standard_user"' in code and '"username": "visual_user"' in code  # each script got its own variation's values
    assert start_run(admin, pid, made["id"]).json()["error"]["code"] == "already_running"
    assert admin.put(url(pid, f"/{made['id']}"), json=body(scripts, ("TC-001", "default"))).json()["error"]["code"] == "already_running"
    assert admin.delete(url(pid, f"/{made['id']}")).status_code == 409
    assert admin.get(url(pid, f"/{made['id']}")).json()["running_job"]["id"] == answer["id"]
    claimed = runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert claimed["payload"]["code"] == code and "provider" not in claimed["payload"]


def finish(runner, steps, **over):
    return claim_and_complete(runner, result(steps=steps, screenshots=[
        {"name": "TMX-1", "content_type": "image/png", "data": PNG},
        {"name": "TMX-2", "content_type": "image/png", "data": PNG},
        {"name": "screenshot", "content_type": "image/png", "data": PNG},
    ], **over))


def test_stop_mode_failed_script_and_the_ones_after_it(monkeypatch, db):
    admin, pid, scripts, ids = make_two(monkeypatch, db)
    made, dv2 = make_batch(admin, pid, scripts, ids)
    runner, _ = online_runner(admin)
    job = start_run(admin, pid, made["id"]).json()
    steps = [marker("START", 1, "TS-001"), step("Step 1: Open"), step("Step 2: Type"), marker("DONE", 1, ms=3200),
             marker("START", 2, "TS-002"), step("Step 1: Open"), step("Step 2: Click", "failed", "element not found"), marker("FAIL", 2, ms=1500)]
    _, done = finish(runner, steps, status="failed", error="element not found", exit_code=1)
    assert done.status_code == 200
    j = admin.get(f"/api/jobs/{job['id']}").json()
    assert j["status"] == "succeeded" and j["result"]["status"] == "failed" and j["result"]["counts"] == {"passed": 1, "failed": 1, "not_run": 1, "aborted": 0}
    page = admin.get(f"/api/projects/{pid}/test-batch-runs/{j['result']['batch_run_id']}").json()
    assert page["key"] == "BR-001" and page["status"] == "failed" and page["on_failure"] == "stop" and page["batch_key"] == "TB-001"
    got = page["items"]
    assert [(i["script_key"], i["status"], i["data_set"] and i["data_set"]["name"]) for i in got] == [
        ("TS-001", "passed", "DV-1 (Default)"), ("TS-002", "failed", None), ("TS-001", "not_run", "DV-2 — Visual User")]
    assert got[0]["duration_seconds"] == 3.2 and got[1]["error"] == "element not found" and got[2]["run_id"] is None
    # every script that ran is an ordinary execution of that script
    runs = db.scalars(select(TsRun).order_by(TsRun.seq)).all()
    assert [(r.status, r.batch_position) for r in runs] == [("passed", 1), ("failed", 2)]
    first = admin.get(f"/api/projects/{pid}/test-runs/{runs[0].id}").json()
    assert first["steps"] == [step("Step 1: Open"), step("Step 2: Type")] and first["data_set"]["name"] == "DV-1 (Default)"
    assert first["batch_run"] == {"id": page["id"], "key": "BR-001", "name": "Register then search", "position": 1}
    assert [a["name"] for a in first["artifacts"]] == ["Page after the script"] and first["script"]["key"] == "TS-001"
    second = admin.get(f"/api/projects/{pid}/test-runs/{runs[1].id}").json()
    assert second["status"] == "failed" and second["error"] == "element not found" and second["steps"][-1]["status"] == "failed"
    history = admin.get(f"/api/projects/{pid}/test-scripts/{scripts['TC-001']['id']}/runs").json()
    assert history["total"] == 1 and history["recent"][0]["batch_run_id"] == page["id"]  # shows in the script's own history
    assert len(db.scalars(select(TsRunArtifact)).all()) == 2  # one page screenshot per script that ran, the end-of-test one is dropped
    listed = admin.get(url(pid)).json()["batches"][0]
    assert listed["last_run"]["key"] == "BR-001" and listed["last_run"]["status"] == "failed"
    assert admin.get(url(pid, f"/{made['id']}")).json()["recent_runs"][0]["counts"]["not_run"] == 1


def test_continue_mode_and_a_passing_run(monkeypatch, db):
    admin, pid, scripts, ids = make_two(monkeypatch, db)
    made, dv2 = make_batch(admin, pid, scripts, ids, on_failure="continue")
    runner, _ = online_runner(admin)
    start_run(admin, pid, made["id"])
    steps = [marker("START", 1, "TS-001"), step("Step 1: A"), marker("DONE", 1, ms=1000),
             marker("START", 2, "TS-002"), step("Step 1: B", "failed", "boom"), marker("FAIL", 2, ms=500),
             marker("START", 3, "TS-001"), step("Step 1: C"), marker("DONE", 3, ms=700)]
    finish(runner, steps, status="failed", error="1 of 3 scripts failed: TS-002", exit_code=1)
    run = db.scalar(select(TbRun))
    assert run.status == "failed" and [i["status"] for i in run.items] == ["passed", "failed", "passed"]  # the third ran after the failure
    assert run.on_failure == "continue" and run.error == "1 of 3 scripts failed: TS-002"
    assert [r.batch_position for r in db.scalars(select(TsRun).order_by(TsRun.seq)).all()] == [1, 2, 3]
    # a second run that passes everywhere
    start_run(admin, pid, made["id"])
    finish(runner, [marker("START", 1, "TS-001"), step("Step 1: A"), marker("DONE", 1), marker("START", 2, "TS-002"), step("Step 1: B"), marker("DONE", 2),
                    marker("START", 3, "TS-001"), step("Step 1: C"), marker("DONE", 3)])
    last = db.scalars(select(TbRun).order_by(TbRun.seq.desc())).first()
    assert last.seq == 2
    assert last.status == "passed" and all(i["status"] == "passed" for i in last.items)


def test_a_script_that_failed_outside_a_step_and_a_cancelled_run(monkeypatch, db):
    admin, pid, scripts, ids = make_two(monkeypatch, db)
    made, dv2 = make_batch(admin, pid, scripts, ids)
    runner, _ = online_runner(admin)
    start_run(admin, pid, made["id"])
    steps = [marker("START", 1, "TS-001"), marker("FAIL", 1, ms=50)]  # nothing inside a step failed: the marker carries the error
    finish(runner, steps, status="failed", error="page.goto: net::ERR_NAME_NOT_RESOLVED", exit_code=1)
    run = db.scalar(select(TbRun))
    assert [i["status"] for i in run.items] == ["failed", "not_run", "not_run"] and run.items[0]["error"] == "element not found"
    own = db.scalar(select(TsRun))
    assert own.steps[-1]["title"] == "Script error" and own.steps[-1]["status"] == "failed"
    # cancelled while the second script was running
    start_run(admin, pid, made["id"])
    finish(runner, [marker("START", 1, "TS-001"), step("Step 1: A"), marker("DONE", 1, ms=900), marker("START", 2, "TS-002"), step("Step 1: B")], status="aborted", exit_code=1)
    last = db.scalars(select(TbRun).order_by(TbRun.seq.desc())).first()
    assert last.status == "aborted" and [i["status"] for i in last.items] == ["passed", "aborted", "not_run"]
    # cancelled: Playwright was killed and wrote no report, so the steps are empty; the console output still shows which script was running
    start_run(admin, pid, made["id"])
    out = "\n".join([
        "Running 1 test using 1 worker",
        "     1.1 TB-001.spec.js:9:1 › Batch TB-001 › Step 0 TMX-START 1 TS-001 (0ms)",
        "     1.2 TB-001.spec.js:9:1 › Batch TB-001 › Step 1: Open (600ms)",
        "     1.3 TB-001.spec.js:9:1 › Batch TB-001 › Step 0 TMX-DONE 1 1200 (0ms)",
        "     1.4 TB-001.spec.js:9:1 › Batch TB-001 › Step 0 TMX-START 2 TS-002 (0ms)",
        "[Stopped: cancelled by the user]",
    ])
    finish(runner, [], status="aborted", exit_code=1, output=out)
    last = db.scalars(select(TbRun).order_by(TbRun.seq.desc())).first()
    assert last.status == "aborted" and [i["status"] for i in last.items] == ["passed", "aborted", "not_run"]
    # the runner ended the run before any script started (for example the browser would not open)
    start_run(admin, pid, made["id"])
    finish(runner, [], status="failed", error="Browser could not start", exit_code=1)
    last = db.scalars(select(TbRun).order_by(TbRun.seq.desc())).first()
    assert last.status == "failed" and {i["status"] for i in last.items} == {"not_run"} and last.error == "Browser could not start"


def test_a_batch_that_cannot_run_says_why(monkeypatch, db):
    admin, pid, scripts, ids = make_two(monkeypatch, db)
    made, dv2 = make_batch(admin, pid, scripts, ids)
    online_runner(admin)
    # a variation that was deleted from the test case since the batch was made
    admin.put(f"/api/projects/{pid}/test-cases/{ids['TC-001']}/data-sets", json={"data_sets": []})
    err = start_run(admin, pid, made["id"]).json()["error"]
    assert err["code"] == "data_set_not_found" and "TS-001" in err["message"]
    assert admin.get(url(pid, f"/{made['id']}")).json()["items"][2]["data_set_missing"] is True
    assert db.scalars(select(Job).where(Job.type == "run_script")).all() == []  # nothing was queued
    # a script that needs test data
    db.get(TsScript, scripts["TC-002"]["id"]).blocked_message = "needs data"
    db.commit()
    fixed = admin.put(url(pid, f"/{made['id']}"), json=body(scripts, ("TC-001", "default"), ("TC-002", "default"))).json()
    err = start_run(admin, pid, fixed["id"]).json()["error"]
    assert err["code"] == "needs_test_data" and "TS-002" in err["message"]


def test_deleting_a_script_shortens_its_batches_and_a_running_batch_blocks_it(monkeypatch, db):
    admin, pid, scripts, ids = make_two(monkeypatch, db)
    made, dv2 = make_batch(admin, pid, scripts, ids)
    runner, _ = online_runner(admin)
    start_run(admin, pid, made["id"])
    blocked = admin.post(f"/api/projects/{pid}/test-scripts/delete", json={"ids": [scripts["TC-002"]["id"]]}).json()
    assert blocked["deleted"] == [] and len(blocked["blocked"]) == 1  # the batch job is running: nothing it uses may be deleted under it
    assert admin.post(f"/api/projects/{pid}/test-cases/delete", json={"ids": [ids["TC-002"]]}).json()["blocked"]
    finish(runner, [marker("START", 1, "TS-001"), step("Step 1: A"), marker("DONE", 1), marker("START", 2, "TS-002"), step("Step 1: B"), marker("DONE", 2),
                    marker("START", 3, "TS-001"), step("Step 1: C"), marker("DONE", 3)])
    gone = admin.post(f"/api/projects/{pid}/test-scripts/delete", json={"ids": [scripts["TC-002"]["id"]]}).json()
    assert len(gone["deleted"]) == 1
    batch = admin.get(url(pid, f"/{made['id']}")).json()
    assert [i["script_key"] for i in batch["items"]] == ["TS-001", "TS-001"] and [i["position"] for i in batch["items"]] == [1, 2]
    run_id = db.scalar(select(TbRun.id))
    page = admin.get(f"/api/projects/{pid}/test-batch-runs/{run_id}").json()
    assert [i["script_key"] for i in page["items"]] == ["TS-001", "TS-002", "TS-001"]  # the old result still names the deleted script
    assert page["items"][1]["run_id"] is None and page["items"][0]["run_id"]  # its execution went with it; the other scripts' stay
    # deleting the batch removes its run results; the scripts and their executions stay
    assert admin.delete(url(pid, f"/{made['id']}")).status_code == 204
    db.expire_all()
    assert db.scalars(select(TbRun)).all() == [] and len(db.scalars(select(TsRun)).all()) == 2
    assert all(r.batch_run_id is None for r in db.scalars(select(TsRun)).all())
    # a batch whose scripts are all gone cannot run
    empty = admin.post(url(pid), json=body(scripts, ("TC-001", "default"))).json()
    admin.post(f"/api/projects/{pid}/test-scripts/delete", json={"ids": [scripts["TC-001"]["id"]]})
    assert admin.get(url(pid, f"/{empty['id']}")).json()["items"] == []
    assert start_run(admin, pid, empty["id"]).json()["error"]["code"] == "empty_batch"
