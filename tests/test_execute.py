"""Execute a script on a runner and the execution report.

Server side: starting an execution, claiming and completing the job as a runner, storing the report, isolation.
Runner side: the real Playwright Test is run on tiny scripts that need no browser (so the test needs Node and the
runner/test-runtime install, and is skipped without them)."""
import base64
import shutil
import threading
import time

import pytest
from sqlalchemy import select, text

from app import jobs, run_service
from app.db import get_engine
from app.models_tests import TcCase, TsRun, TsRunArtifact, TsScript
from app.models_work import Job
from app.worker import new_session, run_job
from runner import test_run
from tests.conftest import new_client, signup_org
from tests.test_runners import pair
from tests.test_tsg import BODY, SKIP_BODY, fake_script_converse, run_script_job, seed_cases, start

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 100).decode()


def make_script(monkeypatch, db, body=BODY, key="TC-001"):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    fake_script_converse(monkeypatch, [body])
    start(admin, pid, ids[key])
    run_script_job()
    script = admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]
    return admin, pid, script, ids


def online_runner(admin, version="0.2.0"):
    runner, runner_id, _ = pair(admin)
    assert runner.post("/api/runner/heartbeat", json={"version": version, "os": "Test OS"}).status_code == 200
    return runner, runner_id


def execute(admin, pid, script_id):
    return admin.post(f"/api/projects/{pid}/test-scripts/{script_id}/execute")


def result(status="passed", **over):
    return {
        "status": status, "exit_code": 0 if status == "passed" else 1, "headed": False,
        "started_at": "2026-11-14T10:00:00+00:00", "finished_at": "2026-11-14T10:00:05+00:00", "duration_seconds": 5.2,
        "output": "Running 1 test using 1 worker\n  ok 1 TC-001.spec.js\n  1 passed (5s)",
        "steps": [{"title": "Step 1: Navigate to the application URL.", "status": "passed", "duration_ms": 1300, "error": ""},
                  {"title": "Step 2: Enter the \"Username\" field.", "status": "passed", "duration_ms": 120, "error": ""}],
        "error": "", "screenshots": [{"name": "screenshot", "content_type": "image/png", "data": PNG}], **over,
    }


def claim_and_complete(runner, payload):
    claimed = runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert claimed and claimed["type"] == "run_script"
    return claimed, runner.post(f"/api/runner/jobs/{claimed['id']}/complete", json={"result": payload})


# ---- starting an execution ----------------------------------------------------------------
def test_execute_needs_an_online_up_to_date_runner(monkeypatch, db):
    admin, pid, script, ids = make_script(monkeypatch, db)
    assert execute(admin, pid, script["id"]).json()["error"]["code"] == "no_runner_online"
    runner, _ = online_runner(admin, version="0.1.0")  # an old runner would answer "does not know the job type"
    err = execute(admin, pid, script["id"]).json()["error"]
    assert err["code"] == "runner_outdated" and "python -m runner run" in err["message"]
    runner.post("/api/runner/heartbeat", json={"version": "0.2.0"})
    assert execute(admin, pid, script["id"]).status_code == 202
    assert [run_service.version_ok(v) for v in ("0.1.9", "0.2.0", "0.10.0", "1.0", "x", "")] == [False, True, True, True, False, False]


def test_execute_hands_the_script_to_the_runner_and_runs_it_once(monkeypatch, db):
    admin, pid, script, ids = make_script(monkeypatch, db)
    runner, _ = online_runner(admin)
    started = execute(admin, pid, script["id"])
    assert started.status_code == 202 and started.json()["script_id"] == script["id"]
    assert execute(admin, pid, script["id"]).json()["error"]["code"] == "already_running"
    job = db.get(Job, started.json()["id"])
    code = admin.get(f"/api/projects/{pid}/test-scripts/{script['id']}").json()["code"]
    assert job.max_attempts == 1 and job.payload["code"] == code and job.payload["file_name"] == "TC-001.spec.js"
    claimed = runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert claimed["payload"]["code"] == code and "provider" not in claimed["payload"]  # no AI settings reach the runner
    # a run that loses its runner is not repeated on its own: it may already have changed data in the application
    db.execute(text("update jobs set lease_expires_at = now() - interval '1 minute' where id = :i"), {"i": job.id})
    db.commit()
    with new_session() as s:
        recovered = jobs.reap_expired(s)
    assert recovered["failed"] == [job.id] and recovered["requeued"] == []
    assert "Please start it again" in admin.get(f"/api/jobs/{job.id}").json()["error"]


def test_a_script_that_needs_test_data_is_not_executed(monkeypatch, db):
    admin, pid, script, ids = make_script(monkeypatch, db, body=SKIP_BODY)
    online_runner(admin)
    case = db.get(TcCase, ids["TC-001"])
    case.steps = [dict(s, testData="<authorized password>") if s["stepNo"] == 2 else s for s in case.steps]
    db.commit()
    fake_script_converse(monkeypatch, [SKIP_BODY])
    start(admin, pid, ids["TC-001"])
    run_script_job()
    r = execute(admin, pid, script["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "needs_test_data" and "Scan App" in r.json()["error"]["message"]
    assert db.scalars(select(Job).where(Job.type == "run_script")).all() == []  # no browser job was queued


# ---- the report ------------------------------------------------------------------------------
def test_completed_run_is_saved_with_steps_log_and_screenshot(monkeypatch, db):
    admin, pid, script, ids = make_script(monkeypatch, db)
    runner, runner_id = online_runner(admin)
    started = execute(admin, pid, script["id"]).json()
    claimed, done = claim_and_complete(runner, result())
    assert done.status_code == 200 and done.json()["status"] == "succeeded"
    j = admin.get(f"/api/jobs/{started['id']}").json()
    assert j["status"] == "succeeded" and j["result"]["run_key"] == "EX-001" and j["result"]["status"] == "passed"
    run_id = j["result"]["run_id"]
    report = admin.get(f"/api/projects/{pid}/test-runs/{run_id}").json()
    assert report["key"] == "EX-001" and report["status"] == "passed" and report["duration_seconds"] == 5.2 and report["headed"] is False
    assert [s["status"] for s in report["steps"]] == ["passed", "passed"] and "1 passed" in report["log"]
    assert report["script"]["key"] == "TS-001" and report["test_case"]["key"] == "TC-001" and report["test_case"]["step_count"] == 3
    assert report["runner"] == "Laptop" and report["executed_by"]
    shot = report["artifacts"][0]
    img = admin.get(f"/api/projects/{pid}/test-runs/{run_id}/artifacts/{shot['id']}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content.startswith(b"\x89PNG")
    assert db.scalar(select(TsRun)).script_code == admin.get(f"/api/projects/{pid}/test-scripts/{script['id']}").json()["code"]


def test_history_pass_rate_list_columns_and_numbering(monkeypatch, db):
    admin, pid, script, ids = make_script(monkeypatch, db)
    runner, _ = online_runner(admin)
    for status, secs in (("passed", 10), ("failed", 20), ("aborted", 1)):
        execute(admin, pid, script["id"])
        claim_and_complete(runner, result(status, duration_seconds=secs))
    stats = admin.get(f"/api/projects/{pid}/test-scripts/{script['id']}/runs").json()
    # aborted runs count as runs but are not part of the pass rate or the average
    assert (stats["total"], stats["passed"], stats["pass_rate"], stats["avg_seconds"]) == (3, 1, 50, 15.0)
    assert [r["key"] for r in stats["recent"]] == ["EX-003", "EX-002", "EX-001"] and stats["recent"][0]["status"] == "aborted"
    one = admin.get(f"/api/projects/{pid}/test-scripts/{script['id']}").json()
    assert one["run_stats"]["total"] == 3
    row = admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]
    assert row["last_run"]["key"] == "EX-003" and row["last_run"]["status"] == "aborted"


def test_the_server_does_not_trust_what_the_runner_says(monkeypatch, db):
    admin, pid, script, ids = make_script(monkeypatch, db)
    runner, _ = online_runner(admin)
    execute(admin, pid, script["id"])
    junk = result("exploded", steps=[{"title": "<script>x</script>", "status": "weird"}, "junk", 5], screenshots=[
        {"name": "ok", "content_type": "image/png", "data": PNG},
        {"name": "svg", "content_type": "image/svg+xml", "data": PNG},
        {"name": "bad base64", "content_type": "image/png", "data": "***"},
        {"name": "big", "content_type": "image/png", "data": base64.b64encode(b"x" * 2_100_000).decode()},
        "junk",
    ])
    claim_and_complete(runner, junk)
    run = db.scalar(select(TsRun))
    assert run.status == "failed"  # an unknown status is never shown as a pass
    assert run.steps == [{"title": "<script>x</script>", "status": "passed", "duration_ms": 0, "error": ""}]
    assert [a.name for a in db.scalars(select(TsRunArtifact)).all()] == ["ok"]  # only small real images are kept


def test_a_deleted_script_fails_the_job_clearly(monkeypatch, db):
    admin, pid, script, ids = make_script(monkeypatch, db)
    runner, _ = online_runner(admin)
    started = execute(admin, pid, script["id"]).json()
    # the API refuses to delete a case whose execution is queued (test_delete.py), so remove the row behind its back
    assert admin.delete(f"/api/projects/{pid}/test-cases/{ids['TC-001']}").status_code == 409
    db.delete(db.get(TcCase, ids["TC-001"]))
    db.commit()
    claim_and_complete(runner, result())
    j = admin.get(f"/api/jobs/{started['id']}").json()
    assert j["status"] == "failed" and "deleted" in j["error"] and db.scalars(select(TsRun)).all() == []


def test_deleting_the_test_case_deletes_runs_and_screenshots(monkeypatch, db):
    admin, pid, script, ids = make_script(monkeypatch, db)
    runner, _ = online_runner(admin)
    execute(admin, pid, script["id"])
    claim_and_complete(runner, result())
    assert db.scalars(select(TsRunArtifact)).all()
    admin.delete(f"/api/projects/{pid}/test-cases/{ids['TC-001']}")
    db.expire_all()
    assert db.scalars(select(TsRun)).all() == [] and db.scalars(select(TsRunArtifact)).all() == []


def test_other_organizations_cannot_run_read_or_claim(monkeypatch, db):
    admin, pid, script, ids = make_script(monkeypatch, db)
    runner, _ = online_runner(admin)
    started = execute(admin, pid, script["id"]).json()
    other = new_client()
    assert signup_org(other, org="Other Inc", email="boss@other.example").status_code == 201
    other_runner, _ = online_runner(other)
    opid = other.post("/api/projects", json={"name": "Theirs"}).json()["id"]
    assert other_runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"] is None  # never sees our run job
    claimed = runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    stolen = other_runner.post(f"/api/runner/jobs/{claimed['id']}/complete", json={"result": result()})
    assert stolen.status_code == 409
    claim_and_complete_done = runner.post(f"/api/runner/jobs/{claimed['id']}/complete", json={"result": result()})
    assert claim_and_complete_done.status_code == 200
    run_id = admin.get(f"/api/jobs/{started['id']}").json()["result"]["run_id"]
    art = admin.get(f"/api/projects/{pid}/test-runs/{run_id}").json()["artifacts"][0]["id"]
    for url in (f"/api/projects/{pid}/test-runs/{run_id}", f"/api/projects/{pid}/test-runs/{run_id}/artifacts/{art}", f"/api/projects/{pid}/test-scripts/{script['id']}/runs"):
        assert other.get(url).status_code == 404, url
    for url in (f"/api/projects/{opid}/test-runs/{run_id}", f"/api/projects/{opid}/test-runs/{run_id}/artifacts/{art}", f"/api/projects/{opid}/test-scripts/{script['id']}/runs"):
        assert other.get(url).status_code == 404, url
    assert other.post(f"/api/projects/{pid}/test-scripts/{script['id']}/execute").status_code == 404
    assert other.post(f"/api/projects/{opid}/test-scripts/{script['id']}/execute").status_code == 404
    assert new_client().get(f"/api/projects/{pid}/test-runs/{run_id}").status_code == 401
    assert admin.get(f"/api/projects/{pid}/test-runs/{run_id}/artifacts/{art}").status_code == 200


# ---- the runner side, with the real Playwright Test (no browser needed for these scripts) --------
needs_playwright = pytest.mark.skipif(shutil.which("node") is None or not test_run.PLAYWRIGHT_CLI.exists(), reason="Node and runner/test-runtime are needed")

PASSING = """const { test } = require('@playwright/test');
test('TC-001 passes', async () => {
  await test.step('Step 1: Do the first thing.', async () => {});
  await test.step('Step 2: Do the second thing.', async () => {});
});
"""
FAILING = """const { test, expect } = require('@playwright/test');
test('TC-002 fails', async () => {
  await test.step('Step 1: Fine.', async () => {});
  await test.step('Step 2: Check the heading.', async () => { expect('Welcome').toBe('Goodbye'); });
});
"""
SKIPPING = """const { test } = require('@playwright/test');
test('TC-003 skips', async () => {
  test.skip(true, 'Test data missing: <authorized password>');
});
"""
SLOW = """const { test } = require('@playwright/test');
test('TC-004 is slow', async () => {
  await test.step('Step 1: Wait.', async () => { await new Promise((r) => setTimeout(r, 60000)); });
});
"""


def go(code, cancelled=lambda: False, reports=None):
    job = {"payload": {"code": code, "file_name": "TC-001.spec.js"}}
    return test_run.execute(job, headless=True, cancelled=cancelled, report=(reports.append if reports is not None else lambda m: None))


@needs_playwright
def test_a_passing_script_reports_each_step_and_live_progress():
    reports = []
    before = set(test_run.RUNS_DIR.glob("*")) if test_run.RUNS_DIR.exists() else set()
    r = go(PASSING, reports=reports)
    assert r["status"] == "passed" and r["exit_code"] == 0 and r["headed"] is False and r["error"] == ""
    assert [(s["title"], s["status"]) for s in r["steps"]] == [("Step 1: Do the first thing.", "passed"), ("Step 2: Do the second thing.", "passed")]
    assert "1 passed" in r["output"] and "\x1b" not in r["output"]
    assert reports[0] == "Starting Chrome" and any(m.startswith("Finished: Step 2") for m in reports)
    assert r["duration_seconds"] > 0
    assert (set(test_run.RUNS_DIR.glob("*")) if test_run.RUNS_DIR.exists() else set()) == before  # the run folder is removed


@needs_playwright
def test_a_failing_step_is_pinpointed_with_its_error():
    r = go(FAILING)
    assert r["status"] == "failed" and r["exit_code"] == 1
    assert [s["status"] for s in r["steps"]] == ["passed", "failed"]
    assert "Goodbye" in r["steps"][1]["error"] and "Goodbye" in r["error"] and "1 failed" in r["output"]


@needs_playwright
def test_a_script_that_skips_itself_is_reported_as_skipped_not_passed():
    r = go(SKIPPING)
    assert r["status"] == "skipped" and r["exit_code"] == 0


@needs_playwright
def test_cancel_stops_the_run_and_reports_aborted():
    flag = threading.Event()
    threading.Timer(3.0, flag.set).start()
    started = time.monotonic()
    r = go(SLOW, cancelled=flag.is_set)
    assert r["status"] == "aborted" and time.monotonic() - started < 40 and "cancelled by the user" in r["output"]


def test_run_setup_problems_are_reported_in_plain_words(monkeypatch):
    with pytest.raises(test_run.RunSetupError, match="no script"):
        go("   ")
    monkeypatch.setattr(test_run.shutil, "which", lambda name: None)
    with pytest.raises(test_run.RunSetupError, match="Node.js was not found"):
        go(PASSING)
    monkeypatch.undo()
    monkeypatch.setattr(test_run, "PLAYWRIGHT_CLI", test_run.RUNNER_DIR / "nope" / "cli.js")
    with pytest.raises(test_run.RunSetupError, match="npm install"):
        go(PASSING)


def test_the_test_process_never_gets_api_keys_and_a_bad_file_name_is_replaced(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret2")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("NO_COLOR", "1")
    env = test_run.clean_environment(headless=False)
    assert not any(k in env for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "NO_COLOR"))
    assert env["TESTMAX_HEADLESS"] == "0" and env["PLAYWRIGHT_LIST_PRINT_STEPS"] == "1" and env["TESTMAX_SLOWMO"] == "300"
    assert test_run.clean_environment(headless=True)["TESTMAX_SLOWMO"] == "0"
    assert not test_run.SAFE_FILE_NAME.match("../x.spec.js") and test_run.SAFE_FILE_NAME.match("TC-001.spec.js")


@pytest.mark.parametrize("exit_code, output, expected", [(0, "  1 passed (4s)", "passed"), (1, "  1 failed", "failed"), (0, "  1 skipped", "skipped"), (0, "  1 skipped\n  1 passed", "passed")])
def test_classify_run_matches_pilot1(exit_code, output, expected):
    assert test_run.classify_run(exit_code, output) == expected


def test_screenshots_outside_the_run_folder_are_never_read(tmp_path):
    inside = tmp_path / "a.png"
    inside.write_bytes(b"\x89PNG" + b"x" * 10)
    outside = tmp_path.parent / "secret.png"
    outside.write_bytes(b"\x89PNG" + b"y" * 10)
    work = tmp_path
    shots = test_run.collect_screenshots([{"path": str(inside), "contentType": "image/png", "name": "ok"}, {"path": str(outside), "contentType": "image/png", "name": "no"}], work)
    assert [s["name"] for s in shots] == ["ok"]
