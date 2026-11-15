import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import jobs
from app.db import get_engine
from app.models import Tenant
from app.models_runner import PairingRequest, Runner
from app.models_work import Job
from tests.conftest import GOOD_PASSWORD, login, new_client, signup_org


def org(name="Acme Corp", email="owner@acme.example"):
    c = new_client()
    assert signup_org(c, org=name, email=email).status_code == 201
    return c


def pair(admin, name="Laptop", capacity=2):
    """Full device-code pairing; returns (runner client with key header, runner id, key)."""
    r = new_client()
    start = r.post("/api/runner/pair/start", json={"name": "ignored-suggestion", "capacity": capacity, "os": "Windows", "version": "0.1"})
    assert start.status_code == 201
    s = start.json()
    approved = admin.post("/api/runners/pair", json={"user_code": s["user_code"], "name": name})
    assert approved.status_code == 201, approved.text
    got = r.post("/api/runner/pair/poll", json={"device_code": s["device_code"]}).json()
    assert got["status"] == "approved"
    r.headers["Authorization"] = f"Bearer {got['runner_key']}"
    return r, got["runner_id"], got["runner_key"]


def add_job(tenant_name, job_type="runner_stub", payload=None):
    with Session(get_engine()) as s:
        t = s.scalar(select(Tenant).where(Tenant.name == tenant_name))
        return jobs.enqueue(s, tenant_id=t.id, job_type=job_type, payload=payload or {"seconds": 1}).id


def job(job_id):
    with Session(get_engine()) as s:
        return s.get(Job, job_id)


# ---- pairing ----------------------------------------------------------------
def test_device_code_pairing_end_to_end(db):
    admin = org()
    r = new_client()
    s = r.post("/api/runner/pair/start", json={"capacity": 3, "os": "Windows 11", "version": "0.1"}).json()
    assert len(s["user_code"]) == 9 and s["user_code"][4] == "-" and s["expires_in"] == 600
    assert r.post("/api/runner/pair/poll", json={"device_code": s["device_code"]}).json() == {"status": "pending"}
    ok = admin.post("/api/runners/pair", json={"user_code": s["user_code"].lower().replace("-", " "), "name": "Azan laptop"})
    assert ok.status_code == 201 and ok.json()["name"] == "Azan laptop" and ok.json()["capacity"] == 3 and ok.json()["online"] is False
    got = r.post("/api/runner/pair/poll", json={"device_code": s["device_code"]}).json()
    assert got["status"] == "approved" and got["runner_key"].startswith("tmr_")
    # the key is handed over once and never stored
    again = r.post("/api/runner/pair/poll", json={"device_code": s["device_code"]})
    assert again.status_code == 410 and again.json()["error"]["code"] == "pairing_used"
    runner = db.scalar(select(Runner))
    assert runner.key_hash == hashlib.sha256(got["runner_key"].encode()).hexdigest() and got["runner_key"] not in (runner.key_hash, runner.name)
    assert db.scalar(select(PairingRequest.pending_key)) is None


def test_pairing_rules():
    admin = org()
    r = new_client()
    s = r.post("/api/runner/pair/start", json={}).json()
    assert admin.post("/api/runners/pair", json={"user_code": "AAAA-BBBB"}).status_code == 404
    assert admin.post("/api/runners/pair", json={"user_code": "nonsense"}).status_code == 404
    admin.post("/api/users", json={"name": "Una", "email": "una@acme.example", "password": GOOD_PASSWORD})
    una = new_client()
    login(una, "una@acme.example")
    assert una.post("/api/runners/pair", json={"user_code": s["user_code"]}).status_code == 403  # admins only
    assert new_client().post("/api/runners/pair", json={"user_code": s["user_code"]}).status_code == 401
    with Session(get_engine()) as db:
        db.execute(text("update pairing_requests set expires_at = now() - interval '1 minute'"))
        db.commit()
    assert admin.post("/api/runners/pair", json={"user_code": s["user_code"]}).status_code == 404  # expired
    assert r.post("/api/runner/pair/poll", json={"device_code": s["device_code"]}).status_code == 410
    assert r.post("/api/runner/pair/poll", json={"device_code": "unknown"}).status_code == 404


def test_code_can_only_be_approved_once():
    admin = org()
    other = org("Other Co", "o@other.example")
    s = new_client().post("/api/runner/pair/start", json={}).json()
    assert admin.post("/api/runners/pair", json={"user_code": s["user_code"]}).status_code == 201
    assert other.post("/api/runners/pair", json={"user_code": s["user_code"]}).status_code == 404


# ---- runner authentication and management ----------------------------------
def test_runner_endpoints_need_a_valid_key():
    org()
    c = new_client()
    assert c.post("/api/runner/heartbeat", json={}).status_code == 401
    c.headers["Authorization"] = "Bearer tmr_not-a-real-key"
    assert c.post("/api/runner/heartbeat", json={}).json()["error"]["code"] == "runner_not_authenticated"
    assert c.post("/api/runner/claim", json={"free_slots": 1}).status_code == 401


def test_online_status_and_listing():
    admin = org()
    runner, rid, _ = pair(admin)
    assert admin.get("/api/runners").json()["runners"][0]["online"] is False  # paired, not seen yet
    assert runner.post("/api/runner/heartbeat", json={"capacity": 4, "os": "Windows 11", "version": "0.2"}).status_code == 200
    row = admin.get("/api/runners").json()["runners"][0]
    assert row["online"] is True and row["capacity"] == 4 and row["info"]["version"] == "0.2"
    st = admin.get("/api/runners/status").json()
    assert (st["total"], st["online"]) == (1, 1) and [(r["name"], r["online"]) for r in st["runners"]] == [("Laptop", True)]
    with Session(get_engine()) as db:
        db.execute(text("update runners set last_seen_at = now() - interval '2 minutes'"))
        db.commit()
    st = admin.get("/api/runners/status").json()
    assert (st["total"], st["online"]) == (1, 0) and st["runners"][0]["online"] is False


def test_only_admins_list_runners_but_everyone_sees_status():
    admin = org()
    pair(admin)
    admin.post("/api/users", json={"name": "Una", "email": "una@acme.example", "password": GOOD_PASSWORD})
    una = new_client()
    login(una, "una@acme.example")
    assert una.get("/api/runners").status_code == 403
    assert una.get("/api/runners/status").json()["total"] == 1


def test_revoked_runner_is_refused_at_once():
    admin = org()
    runner, rid, _ = pair(admin)
    runner.post("/api/runner/heartbeat", json={})
    assert admin.delete(f"/api/runners/{rid}").status_code == 204
    r = runner.post("/api/runner/heartbeat", json={})
    assert r.status_code == 401 and r.json()["error"]["code"] == "runner_revoked"
    assert runner.post("/api/runner/claim", json={"free_slots": 1}).status_code == 401
    assert admin.get("/api/runners").json()["runners"] == []
    assert admin.delete(f"/api/runners/{rid}").status_code == 404


def test_admin_cannot_revoke_another_organizations_runner():
    a = org("Tenant A", "a@a.example")
    b = org("Tenant B", "b@b.example")
    runner_b, rid_b, _ = pair(b)
    assert a.delete(f"/api/runners/{rid_b}").status_code == 404
    assert runner_b.post("/api/runner/heartbeat", json={}).status_code == 200
    assert a.get("/api/runners").json()["runners"] == [] and a.get("/api/runners/status").json()["total"] == 0


# ---- claiming ---------------------------------------------------------------
def test_runner_claims_only_its_own_organizations_runner_jobs():
    a = org("Tenant A", "a@a.example")
    b = org("Tenant B", "b@b.example")
    runner_a, _, _ = pair(a)
    runner_b, _, _ = pair(b)
    ja = add_job("Tenant A")
    jb = add_job("Tenant B")
    add_job("Tenant A", job_type="generate_requirements")  # a server-worker job: never given to a runner
    got_a = runner_a.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert got_a["id"] == str(ja) and got_a["type"] == "runner_stub"
    assert runner_a.post("/api/runner/claim", json={"free_slots": 1}).json()["job"] is None  # nothing else for A
    got_b = runner_b.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert got_b["id"] == str(jb)
    j = job(ja)
    assert j.status == "running" and j.worker_id.startswith("runner:") and j.runner_id is not None


def test_no_free_slot_means_no_claim():
    admin = org()
    runner, _, _ = pair(admin)
    add_job("Acme Corp")
    assert runner.post("/api/runner/claim", json={"free_slots": 0}).json()["job"] is None
    assert runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"] is not None


def test_server_workers_never_claim_runner_jobs():
    org()
    add_job("Acme Corp", job_type="app_scan")
    with Session(get_engine()) as s:
        from app.handlers import HANDLERS

        assert jobs.claim(s, "server-worker", list(HANDLERS)) is None


def test_per_organization_cap_applies_to_runners(monkeypatch):
    monkeypatch.setenv("JOB_TENANT_CONCURRENCY", "1")
    admin = org()
    runner, _, _ = pair(admin)
    j1 = add_job("Acme Corp")
    add_job("Acme Corp")
    assert runner.post("/api/runner/claim", json={"free_slots": 2}).json()["job"]["id"] == str(j1)
    assert runner.post("/api/runner/claim", json={"free_slots": 2}).json()["job"] is None


# ---- job lifecycle on the runner -------------------------------------------
def test_stub_job_lifecycle_progress_heartbeat_complete():
    admin = org()
    runner, _, _ = pair(admin, "Lab runner")
    jid = add_job("Acme Corp")
    claimed = runner.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert claimed["lease_seconds"] > 0 and claimed["heartbeat_seconds"] > 0
    assert runner.post(f"/api/runner/jobs/{jid}/progress", json={"message": "Working"}).status_code == 204
    assert job(jid).progress == "Working"
    with Session(get_engine()) as s:
        s.execute(text("update jobs set lease_expires_at = now() + interval '1 second' where id=:i"), {"i": jid})
        s.commit()
    hb = runner.post(f"/api/runner/jobs/{jid}/heartbeat").json()
    assert hb == {"ok": True, "status": "running", "cancel_requested": False}
    assert job(jid).lease_expires_at > datetime.now(timezone.utc) + timedelta(seconds=10)
    done = runner.post(f"/api/runner/jobs/{jid}/complete", json={"result": {"echo": "hi"}}).json()
    assert done["status"] == "succeeded"
    j = job(jid)
    assert j.result == {"echo": "hi", "runner": "Lab runner"} and j.status == "succeeded"


def test_runner_failure_reports_the_message_and_retryable_requeues():
    admin = org()
    runner, _, _ = pair(admin)
    j1 = add_job("Acme Corp")
    runner.post("/api/runner/claim", json={"free_slots": 1})
    assert runner.post(f"/api/runner/jobs/{j1}/fail", json={"message": "Chrome could not start."}).json()["state"] == "failed"
    assert job(j1).status == "failed" and job(j1).error == "Chrome could not start."
    j2 = add_job("Acme Corp")
    runner.post("/api/runner/claim", json={"free_slots": 1})
    assert runner.post(f"/api/runner/jobs/{j2}/fail", json={"message": "Network blip", "retryable": True}).json()["state"] == "requeued"
    assert job(j2).status == "queued"


def test_a_runner_cannot_touch_another_runners_or_organizations_job():
    a = org("Tenant A", "a@a.example")
    b = org("Tenant B", "b@b.example")
    runner_a1, _, _ = pair(a, "A1")
    runner_a2, _, _ = pair(a, "A2")
    runner_b, _, _ = pair(b)
    jid = add_job("Tenant A")
    runner_a1.post("/api/runner/claim", json={"free_slots": 1})
    for other in (runner_a2, runner_b):
        assert other.post(f"/api/runner/jobs/{jid}/progress", json={"message": "x"}).status_code == 409
        assert other.post(f"/api/runner/jobs/{jid}/complete", json={"result": {}}).status_code == 409
        assert other.post(f"/api/runner/jobs/{jid}/fail", json={"message": "x"}).status_code == 409
        assert other.post(f"/api/runner/jobs/{jid}/heartbeat").status_code == 404
    assert job(jid).status == "running"


def test_a_dead_runners_job_is_recovered_by_another_runner_or_fails_clearly():
    admin = org()
    r1, _, _ = pair(admin, "R1")
    r2, _, _ = pair(admin, "R2")
    jid = add_job("Acme Corp")
    r1.post("/api/runner/claim", json={"free_slots": 1})
    with Session(get_engine()) as s:  # runner 1 dies: its lease runs out
        s.execute(text("update jobs set lease_expires_at = now() - interval '1 second' where id=:i"), {"i": jid})
        s.commit()
        assert jobs.reap_expired(s)["requeued"] == [jid]
    assert r1.post(f"/api/runner/jobs/{jid}/heartbeat").json()["ok"] is False  # a zombie runner is told to stop
    again = r2.post("/api/runner/claim", json={"free_slots": 1}).json()["job"]
    assert again["id"] == str(jid) and again["attempts"] == 2
    assert r1.post(f"/api/runner/jobs/{jid}/complete", json={"result": {}}).status_code == 409  # the old runner cannot finish it
    with Session(get_engine()) as s:
        s.execute(text("update jobs set lease_expires_at = now() - interval '1 second' where id=:i"), {"i": jid})
        s.commit()
        assert jobs.reap_expired(s)["failed"] == [jid]
    j = job(jid)
    assert j.status == "failed" and "runner" in j.error and "did not complete after 2 attempts" in j.error


def test_cancel_queued_and_running_jobs():
    admin = org()
    runner, _, _ = pair(admin)
    queued = add_job("Acme Corp")
    with Session(get_engine()) as s:
        assert jobs.request_cancel(s, queued) == "cancelled"
    assert job(queued).status == "failed" and job(queued).error == "Cancelled by the user."
    running = add_job("Acme Corp")
    runner.post("/api/runner/claim", json={"free_slots": 1})
    with Session(get_engine()) as s:
        assert jobs.request_cancel(s, running) == "cancelling"
    assert runner.post(f"/api/runner/jobs/{running}/heartbeat").json()["cancel_requested"] is True
    with Session(get_engine()) as s:
        jobs.succeed(s, running, {})
        assert jobs.request_cancel(s, running) == "finished"
