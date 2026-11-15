"""Choosing the runner for a scan or an execution, and the per-organization limit of jobs running at the same time."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import jobs
from app.db import get_engine
from app.models import Tenant
from app.models_work import Job
from tests.conftest import GOOD_PASSWORD, login, new_client, signup_org
from tests.test_execute import claim_and_complete, execute, result
from tests.test_runners import add_job, job, org, pair
from tests.test_scan import add_requirement, body, scan_url, setup_scan
from tests.test_tsg import BODY, fake_script_converse, run_script_job, seed_cases, start


def online(admin, name, version="0.3.0", capacity=2):
    runner, runner_id, _ = pair(admin, name=name, capacity=capacity)
    assert runner.post("/api/runner/heartbeat", json={"version": version, "os": "Test OS"}).status_code == 200
    return runner, runner_id


def script_org(monkeypatch, db):
    """An organization with one script that can be executed."""
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    fake_script_converse(monkeypatch, [BODY])
    start(admin, pid, ids["TC-001"])
    run_script_job()
    script = admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"][0]
    return admin, pid, script


def run_for(admin, pid, script_id, runner_id=None):
    json = None if runner_id is None else {"runner_id": runner_id}
    return admin.post(f"/api/projects/{pid}/test-scripts/{script_id}/execute", json=json)


# ---- choosing a runner for an execution ---------------------------------------------------------
def test_a_chosen_runner_is_the_only_one_that_can_take_the_job(monkeypatch, db):
    admin, pid, script = script_org(monkeypatch, db)
    a, a_id = online(admin, "Office PC")
    b, b_id = online(admin, "Home laptop")
    r = run_for(admin, pid, script["id"], b_id)
    assert r.status_code == 202 and r.json()["target_runner"] == {"id": b_id, "name": "Home laptop"}
    assert a.post("/api/runner/claim", json={"free_slots": 1}).json()["job"] is None  # A may not take it
    claimed = b.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert claimed and claimed["type"] == "run_script"
    done = b.post(f"/api/runner/jobs/{claimed['id']}/complete", json={"result": result()})
    assert done.status_code == 200
    run_id = admin.get(f"/api/jobs/{r.json()['id']}").json()["result"]["run_id"]
    assert admin.get(f"/api/projects/{pid}/test-runs/{run_id}").json()["runner"] == "Home laptop"


def test_without_a_choice_any_runner_may_take_it(monkeypatch, db):
    admin, pid, script = script_org(monkeypatch, db)
    a, a_id = online(admin, "Office PC")
    b, b_id = online(admin, "Home laptop")
    r = execute(admin, pid, script["id"])  # no body at all, as before
    assert r.status_code == 202 and r.json()["target_runner"] is None
    assert a.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]["id"] == r.json()["id"]
    r2 = run_for(admin, pid, script["id"])  # an empty choice is "any runner" too
    assert r2.status_code == 409  # the script is already running: the old rule still holds


def test_a_runner_that_cannot_take_the_job_is_refused_clearly(monkeypatch, db):
    admin, pid, script = script_org(monkeypatch, db)
    old, old_id = online(admin, "Old PC", version="0.1.0")
    never, never_id, _ = pair(admin, name="Never seen")  # paired, never heartbeated: offline
    good, good_id = online(admin, "Good PC")
    out = run_for(admin, pid, script["id"], old_id)
    assert out.status_code == 409 and out.json()["error"]["code"] == "runner_outdated" and "Old PC" in out.json()["error"]["message"]
    out = run_for(admin, pid, script["id"], never_id)
    assert out.status_code == 409 and out.json()["error"]["code"] == "runner_offline" and "Never seen" in out.json()["error"]["message"]
    assert run_for(admin, pid, script["id"], "00000000-0000-4000-8000-000000000000").json()["error"]["code"] == "runner_not_found"
    assert run_for(admin, pid, script["id"], "not-an-id").status_code == 422
    assert run_for(admin, pid, script["id"], good_id).status_code == 202  # nothing was queued by the refused attempts
    with Session(get_engine()) as s:
        assert len(s.scalars(select(Job).where(Job.type == "run_script")).all()) == 1


def test_a_runner_of_another_organization_or_a_revoked_one_cannot_be_chosen(monkeypatch, db):
    admin, pid, script = script_org(monkeypatch, db)
    online(admin, "Mine")
    other = org(name="Other Inc", email="boss@other.example")
    foreign, foreign_id = online(other, "Their runner")
    assert run_for(admin, pid, script["id"], foreign_id).json()["error"]["code"] == "runner_not_found"
    gone, gone_id = online(admin, "Gone soon")
    assert admin.delete(f"/api/runners/{gone_id}").status_code == 204
    assert run_for(admin, pid, script["id"], gone_id).json()["error"]["code"] == "runner_not_found"


def test_revoking_a_runner_fails_the_jobs_that_only_it_could_take(monkeypatch, db):
    admin, pid, script = script_org(monkeypatch, db)
    a, a_id = online(admin, "Office PC")
    b, b_id = online(admin, "Home laptop")
    waiting = run_for(admin, pid, script["id"], b_id).json()
    free = add_job("Acme Corp")  # a job nobody is tied to
    assert admin.delete(f"/api/runners/{b_id}").status_code == 204
    j = admin.get(f"/api/jobs/{waiting['id']}").json()
    assert j["status"] == "failed" and "Home laptop" in j["error"] and "removed" in j["error"]
    assert job(free).status == "queued"  # other jobs are untouched
    assert a.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]["id"] == str(free)


def test_a_chosen_runner_that_goes_quiet_leaves_the_job_waiting_and_cancellable(monkeypatch, db):
    admin, pid, script = script_org(monkeypatch, db)
    a, a_id = online(admin, "Office PC")
    b, b_id = online(admin, "Home laptop")
    queued = run_for(admin, pid, script["id"], b_id).json()
    for _ in range(3):  # A keeps asking; the job stays for B
        assert a.post("/api/runner/claim", json={"free_slots": 1}).json()["job"] is None
    assert admin.get(f"/api/jobs/{queued['id']}").json()["status"] == "queued"
    assert admin.post(f"/api/jobs/{queued['id']}/cancel").status_code == 200
    assert admin.get(f"/api/jobs/{queued['id']}").json()["status"] == "failed"


def test_the_status_lists_the_runners_for_the_run_on_choice(monkeypatch, db):
    admin, pid, script = script_org(monkeypatch, db)
    online(admin, "Zed", version="0.3.0")
    online(admin, "Old", version="0.1.0")
    pair(admin, name="Asleep")
    st = admin.get("/api/runners/status").json()
    assert [(r["name"], r["online"], r["can_run_scripts"]) for r in st["runners"]] == [("Asleep", False, False), ("Old", True, False), ("Zed", True, True)]
    assert st["total"] == 3 and st["online"] == 2
    other = org(name="Other Inc", email="boss@other.example")
    assert other.get("/api/runners/status").json()["runners"] == []


# ---- choosing a runner for a scan -----------------------------------------------------------------
def test_a_scan_can_be_tied_to_one_runner(monkeypatch, db):
    admin, pid = setup_scan()
    req = add_requirement(admin, pid, monkeypatch)
    admin.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "m"})
    a, a_id = online(admin, "Office PC")
    b, b_id = online(admin, "Home laptop")
    never, never_id, _ = pair(admin, name="Never seen")
    u = scan_url(pid, req["id"])
    assert admin.post(u, json=body(runner_id=never_id)).json()["error"]["code"] == "runner_offline"
    ok = admin.post(u, json=body(runner_id=b_id))
    assert ok.status_code == 202
    j = db.scalar(select(Job).where(Job.type == "app_scan"))
    assert j.target_runner_id is not None and str(j.target_runner_id) == b_id and j.payload["target_runner"]["name"] == "Home laptop"
    assert a.post("/api/runner/claim", json={"free_slots": 1}).json()["job"] is None
    assert b.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]["type"] == "app_scan"


# ---- the limit of jobs running at the same time -------------------------------------------------
def tenant_job_count(admin_runner, n):
    got = []
    for _ in range(n):
        j = admin_runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
        if j:
            got.append(j["id"])
    return got


def test_the_limit_is_shown_and_defaults_to_three():
    admin = org()
    v = admin.get("/api/runners/limits").json()
    assert (v["limit"], v["default"], v["max"], v["custom"], v["running"], v["runners_online"], v["runner_slots"]) == (3, 3, 20, False, 0, 0, 0)
    a, _ = online(admin, "A", capacity=2)
    b, _ = online(admin, "B", capacity=3)
    pair(admin, name="Offline", capacity=5)  # not online: its slots do not count
    v = admin.get("/api/runners/limits").json()
    assert v["runners_online"] == 2 and v["runner_slots"] == 5
    for _ in range(5):
        add_job("Acme Corp")
    assert len(tenant_job_count(a, 5)) == 3  # only three run at once, however many slots there are
    assert admin.get("/api/runners/limits").json()["running"] == 3


def test_an_administrator_can_change_the_limit_and_it_takes_effect(monkeypatch):
    admin = org()
    a, _ = online(admin, "A", capacity=8)
    for _ in range(6):
        add_job("Acme Corp")
    r = admin.put("/api/runners/limits", json={"limit": 5})
    assert r.status_code == 200 and r.json()["limit"] == 5 and r.json()["custom"] is True
    assert len(tenant_job_count(a, 6)) == 5
    # lowering it does not stop running jobs; new ones wait until fewer are running
    admin.put("/api/runners/limits", json={"limit": 2})
    add_job("Acme Corp")
    assert tenant_job_count(a, 2) == []
    # back to the default
    r = admin.put("/api/runners/limits", json={"limit": None})
    assert r.json()["limit"] == 3 and r.json()["custom"] is False
    assert admin.put("/api/runners/limits", json={"limit": 3}).json()["custom"] is False  # equal to the default is the default


def test_the_limit_is_validated_and_only_administrators_change_it():
    admin = org()
    for bad in (0, -1, 21, 1000):
        r = admin.put("/api/runners/limits", json={"limit": bad})
        assert r.status_code == 422 and r.json()["error"]["code"] == "bad_limit", bad
    assert admin.put("/api/runners/limits", json={"limit": "many"}).status_code == 422
    assert admin.put("/api/runners/limits", json={"limit": 20}).json()["limit"] == 20
    assert admin.post("/api/users", json={"name": "Una", "email": "una@acme.example", "password": GOOD_PASSWORD}).status_code == 201
    una = new_client()
    login(una, "una@acme.example")
    assert una.put("/api/runners/limits", json={"limit": 2}).status_code == 403
    assert una.get("/api/runners/limits").json()["limit"] == 20  # anyone signed in may read it
    assert new_client().get("/api/runners/limits").status_code == 401
    assert new_client().put("/api/runners/limits", json={"limit": 2}).status_code == 401


def test_one_organizations_limit_never_affects_another(monkeypatch):
    first = org()
    second = org(name="Other Inc", email="boss@other.example")
    a, _ = online(first, "A", capacity=8)
    b, _ = online(second, "B", capacity=8)
    first.put("/api/runners/limits", json={"limit": 1})
    assert second.get("/api/runners/limits").json()["limit"] == 3 and second.get("/api/runners/limits").json()["custom"] is False
    for _ in range(4):
        add_job("Acme Corp")
        add_job("Other Inc")
    assert len(tenant_job_count(a, 4)) == 1  # the first organization is held to its own limit
    assert len(tenant_job_count(b, 4)) == 3  # the second to the default; the first one's running job does not count against it
