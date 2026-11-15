"""Delete requirements, test cases and test scripts, one or many: cascades, running jobs, isolation, validation."""
from sqlalchemy import select

from app import jobs
from app.models import Project
from app.models_runner import Scan
from app.models_tests import TcCase, TcSet, TsRun, TsRunArtifact, TsScript
from app.models_work import Requirement
from tests.conftest import new_client, signup_org
from tests.test_execute import claim_and_complete, execute, online_runner, result
from tests.test_tsg import BODY, fake_script_converse, run_script_job, seed_cases, start


def api(pid, what):
    return f"/api/projects/{pid}/{what}/delete"


def with_scripts(monkeypatch, db):
    """Org with REQ-001, one scan, TC-001 and TC-002, a script for each, and one finished execution of TS-001."""
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    fake_script_converse(monkeypatch, [BODY])
    start(admin, pid, ids["TC-001"], ids["TC-002"])
    run_script_job()
    run_script_job()
    scripts = {s["test_case_key"]: s["id"] for s in admin.get(f"/api/projects/{pid}/test-scripts").json()["scripts"]}
    runner, _ = online_runner(admin)
    execute(admin, pid, scripts["TC-001"])
    claim_and_complete(runner, result())
    return admin, pid, req, ids, scripts


def counts(db):
    db.expire_all()
    return {m.__name__: len(db.scalars(select(m)).all()) for m in (Requirement, Scan, TcSet, TcCase, TsScript, TsRun, TsRunArtifact)}


def test_delete_many_test_cases_removes_scripts_runs_screenshots_and_the_emptied_set(monkeypatch, db):
    admin, pid, req, ids, scripts = with_scripts(monkeypatch, db)
    assert counts(db) == {"Requirement": 2, "Scan": 1, "TcSet": 1, "TcCase": 2, "TsScript": 2, "TsRun": 1, "TsRunArtifact": 1}
    r = admin.post(api(pid, "test-cases"), json={"ids": [ids["TC-001"], ids["TC-002"], "00000000-0000-4000-8000-000000000000"]})
    assert r.status_code == 200
    assert [d["key"] for d in r.json()["deleted"]] == ["TC-001", "TC-002"] and r.json()["blocked"] == [] and r.json()["missing"] == 1
    assert counts(db) == {"Requirement": 2, "Scan": 1, "TcSet": 0, "TcCase": 0, "TsScript": 0, "TsRun": 0, "TsRunArtifact": 0}
    assert admin.get(f"/api/projects/{pid}/test-case-sets").json()["sets"] == []
    assert admin.get(f"/api/projects/{pid}/test-cases").json()["total"] == 0


def test_deleting_some_cases_keeps_the_set_and_the_others(monkeypatch, db):
    admin, pid, req, ids, scripts = with_scripts(monkeypatch, db)
    r = admin.post(api(pid, "test-cases"), json={"ids": [ids["TC-002"]]})
    assert [d["key"] for d in r.json()["deleted"]] == ["TC-002"]
    sets = admin.get(f"/api/projects/{pid}/test-case-sets").json()["sets"]
    assert len(sets) == 1 and sets[0]["test_case_count"] == 1
    keys = [c["key"] for c in admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"]]
    assert keys == ["TC-001"] and counts(db)["TsScript"] == 1 and counts(db)["TsRun"] == 1


def test_delete_many_scripts_keeps_the_test_cases_and_deletes_the_runs(monkeypatch, db):
    admin, pid, req, ids, scripts = with_scripts(monkeypatch, db)
    r = admin.post(api(pid, "test-scripts"), json={"ids": [scripts["TC-001"]]})
    assert [d["key"] for d in r.json()["deleted"]] == ["TS-001"] and r.json()["blocked"] == []
    assert counts(db) == {"Requirement": 2, "Scan": 1, "TcSet": 1, "TcCase": 2, "TsScript": 1, "TsRun": 0, "TsRunArtifact": 0}
    case = admin.get(f"/api/projects/{pid}/test-cases/{ids['TC-001']}").json()
    assert case["script"] is None  # a new script can be generated for it
    assert admin.get(f"/api/projects/{pid}/test-scripts/{scripts['TC-001']}").status_code == 404
    fake_script_converse(monkeypatch, [BODY])
    assert start(admin, pid, ids["TC-001"]).status_code == 202


def test_delete_many_requirements_removes_everything_built_on_them(monkeypatch, db):
    admin, pid, req, ids, scripts = with_scripts(monkeypatch, db)
    r = admin.post(api(pid, "requirements"), json={"ids": [req["id"]]})
    assert r.status_code == 200 and [d["key"] for d in r.json()["deleted"]] == ["REQ-001"] and r.json()["blocked"] == []
    assert counts(db) == {"Requirement": 1, "Scan": 0, "TcSet": 0, "TcCase": 0, "TsScript": 0, "TsRun": 0, "TsRunArtifact": 0}
    assert [x["key"] for x in admin.get(f"/api/projects/{pid}/requirements").json()["requirements"]] == ["REQ-002"]  # the other one is untouched
    assert admin.get(f"/api/projects/{pid}/requirements/{req['id']}").status_code == 404
    assert admin.get(f"/api/projects/{pid}/test-scripts").json()["total"] == 0


def test_an_item_with_a_running_job_is_kept_and_the_rest_is_deleted(monkeypatch, db):
    admin, pid, req, ids, scripts = with_scripts(monkeypatch, db)
    project = db.get(Project, pid)
    fake_script_converse(monkeypatch, [BODY])
    start(admin, pid, ids["TC-002"])  # regenerate TC-002's script: the job stays queued
    r = admin.post(api(pid, "test-cases"), json={"ids": [ids["TC-001"], ids["TC-002"]]})
    assert [d["key"] for d in r.json()["deleted"]] == ["TC-001"]
    assert [b["key"] for b in r.json()["blocked"]] == ["TC-002"] and "still running" in r.json()["blocked"][0]["reason"]
    assert [c["key"] for c in admin.get(f"/api/projects/{pid}/test-cases").json()["test_cases"]] == ["TC-002"]
    # the requirement is blocked by the same job (it sits under it), then free once the job ended
    blocked = admin.post(api(pid, "requirements"), json={"ids": [req["id"]]}).json()
    assert blocked["deleted"] == [] and [b["key"] for b in blocked["blocked"]] == ["REQ-001"]
    run_script_job()
    assert admin.post(api(pid, "requirements"), json={"ids": [req["id"]]}).json()["deleted"][0]["key"] == "REQ-001"
    # a queued scan or test case generation also blocks its requirement
    db.expire_all()


def test_scan_and_generation_jobs_block_their_requirement(monkeypatch, db):
    admin, pid, req, scan, ids = seed_cases(monkeypatch, db)
    project = db.get(Project, pid)
    for job_type in ("app_scan", "generate_test_cases"):
        job = jobs.enqueue(db, tenant_id=project.tenant_id, job_type=job_type, project_id=project.id, created_by=None, payload={"requirement_id": req["id"]})
        r = admin.post(api(pid, "requirements"), json={"ids": [req["id"]]}).json()
        assert r["deleted"] == [] and r["blocked"][0]["key"] == "REQ-001", job_type
        db.execute(jobs.Job.__table__.delete().where(jobs.Job.id == job.id))
        db.commit()
    assert admin.post(api(pid, "requirements"), json={"ids": [req["id"]]}).json()["deleted"][0]["key"] == "REQ-001"


def test_a_running_execution_blocks_deleting_its_script_and_case(monkeypatch, db):
    admin, pid, req, ids, scripts = with_scripts(monkeypatch, db)
    execute(admin, pid, scripts["TC-001"])  # queued for the runner
    s = admin.post(api(pid, "test-scripts"), json={"ids": [scripts["TC-001"]]}).json()
    c = admin.post(api(pid, "test-cases"), json={"ids": [ids["TC-001"]]}).json()
    assert s["deleted"] == [] and s["blocked"][0]["key"] == "TS-001"
    assert c["deleted"] == [] and c["blocked"][0]["key"] == "TC-001"
    assert admin.delete(f"/api/projects/{pid}/test-cases/{ids['TC-001']}").status_code == 409


def test_the_single_delete_still_works(monkeypatch, db):
    admin, pid, req, ids, scripts = with_scripts(monkeypatch, db)
    assert admin.delete(f"/api/projects/{pid}/test-cases/{ids['TC-002']}").status_code == 204
    assert admin.delete(f"/api/projects/{pid}/test-cases/{ids['TC-002']}").status_code == 404


def test_other_organizations_and_other_projects_cannot_delete(monkeypatch, db):
    admin, pid, req, ids, scripts = with_scripts(monkeypatch, db)
    before = counts(db)
    other = new_client()
    assert signup_org(other, org="Other Inc", email="boss@other.example").status_code == 201
    assert other.post(api(pid, "test-cases"), json={"ids": [ids["TC-001"]]}).status_code == 404  # their view: no such project
    other_pid = other.post("/api/projects", json={"name": "Theirs"}).json()["id"]
    for what, victim in (("requirements", req["id"]), ("test-cases", ids["TC-001"]), ("test-scripts", scripts["TC-001"])):
        r = other.post(api(other_pid, what), json={"ids": [victim]})
        assert r.status_code == 200 and r.json()["deleted"] == [] and r.json()["missing"] == 1, what
    # the same organization, another project: ids of project 1 do not work there either
    second = admin.post("/api/projects", json={"name": "Second"}).json()["id"]
    for what, victim in (("requirements", req["id"]), ("test-cases", ids["TC-001"]), ("test-scripts", scripts["TC-001"])):
        assert admin.post(api(second, what), json={"ids": [victim]}).json()["deleted"] == [], what
    assert counts(db) == before


def test_the_request_is_validated(monkeypatch, db):
    admin, pid, req, ids, scripts = with_scripts(monkeypatch, db)
    for what in ("requirements", "test-cases", "test-scripts"):
        assert admin.post(api(pid, what), json={"ids": []}).status_code == 422
        assert admin.post(api(pid, what), json={"ids": ["not-an-id"]}).status_code == 422
        assert admin.post(api(pid, what), json={"ids": ["00000000-0000-4000-8000-000000000000"] * 201}).status_code == 422
        assert admin.post(api(pid, what), json={}).status_code == 422
    assert new_client().post(api(pid, "test-cases"), json={"ids": [ids["TC-001"]]}).status_code == 401
    assert counts(db)["TcCase"] == 2
