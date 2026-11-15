import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import jobs
from app.db import get_engine
from app.models import Tenant
from app.models_work import Job, Requirement
from tests.conftest import new_client, signup_org


def two_tenants(db):
    for i, name in enumerate(["Tenant A", "Tenant B"]):
        signup_org(new_client(), org=name, email=f"o{i}@x.example")
    a = db.scalar(select(Tenant).where(Tenant.name == "Tenant A"))
    b = db.scalar(select(Tenant).where(Tenant.name == "Tenant B"))
    return a.id, b.id


def add(db, tenant_id, n=1, job_type="noop"):
    out = []
    for _ in range(n):
        out.append(jobs.enqueue(db, tenant_id=tenant_id, job_type=job_type, payload={}).id)
    return out


def claim(worker="w1", types=None):
    with Session(get_engine()) as s:
        return jobs.claim(s, worker, types)


def row(job_id):
    with Session(get_engine()) as s:
        return s.get(Job, job_id)


def expire_lease(job_id):
    with Session(get_engine()) as s:
        s.execute(text("update jobs set lease_expires_at = now() - interval '1 second' where id=:i"), {"i": job_id})
        s.commit()


# ---- claim ------------------------------------------------------------------
def test_claim_marks_running_with_lease_and_attempt(db):
    a, _ = two_tenants(db)
    (jid,) = add(db, a)
    assert claim("w1") == jid
    j = row(jid)
    assert j.status == "running" and j.worker_id == "w1" and j.attempts == 1 and j.started_at is not None
    assert j.lease_expires_at > datetime.now(timezone.utc)
    assert claim("w2") is None  # nothing else queued, and a running job is never claimed twice


def test_oldest_job_of_a_tenant_goes_first(db):
    a, _ = two_tenants(db)
    first, second, third = add(db, a, 3)
    assert [claim(), claim(), claim()] == [first, second, third]


def test_claim_respects_job_types(db):
    a, _ = two_tenants(db)
    (other,) = add(db, a, job_type="other")
    (wanted,) = add(db, a, job_type="wanted")
    assert claim(types=["wanted"]) == wanted
    assert claim(types=["wanted"]) is None
    assert claim(types=None) == other


def test_per_tenant_concurrency_cap_and_other_tenants_not_blocked(db, monkeypatch):
    monkeypatch.setenv("JOB_TENANT_CONCURRENCY", "2")
    a, b = two_tenants(db)
    add(db, a, 4)
    (b1,) = add(db, b)
    got = [claim() for _ in range(3)]
    assert sum(1 for g in got if g) == 3
    assert b1 in got  # B is served even though A has more waiting
    assert claim() is None  # A is at its cap of 2 and B has nothing left
    with Session(get_engine()) as s:
        running_a = s.scalar(select(text("count(*)")).select_from(Job).where(Job.tenant_id == a, Job.status == "running"))
    assert running_a == 2


def test_cap_frees_up_when_a_job_finishes(db, monkeypatch):
    monkeypatch.setenv("JOB_TENANT_CONCURRENCY", "1")
    a, _ = two_tenants(db)
    j1, j2 = add(db, a, 2)
    assert claim() == j1 and claim() is None
    with Session(get_engine()) as s:
        jobs.succeed(s, j1, {})
    assert claim() == j2


def test_fairness_tenant_with_fewer_running_jobs_goes_first(db, monkeypatch):
    monkeypatch.setenv("JOB_TENANT_CONCURRENCY", "10")
    a, b = two_tenants(db)
    a1, a2, a3 = add(db, a, 3)  # older
    (b1,) = add(db, b)  # newer
    assert claim() == a1  # both tenants idle: oldest first
    assert claim() == b1  # A now has 1 running, B has 0 -> B jumps ahead of A's older jobs
    assert claim() == a2


def test_many_workers_claim_each_job_exactly_once(db, monkeypatch):
    monkeypatch.setenv("JOB_TENANT_CONCURRENCY", "100")
    a, b = two_tenants(db)
    ids = set(add(db, a, 8) + add(db, b, 8))
    results, errors = [], []
    barrier = threading.Barrier(24)

    def worker(n):
        try:
            barrier.wait()
            got = claim(f"w{n}")
            results.append(got)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(24)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    claimed = [r for r in results if r]
    assert len(claimed) == 16 and set(claimed) == ids  # no job twice, none missed
    assert results.count(None) == 8


# ---- leases, heartbeat, recovery -------------------------------------------
def test_heartbeat_extends_only_the_owners_lease(db):
    a, _ = two_tenants(db)
    (jid,) = add(db, a)
    claim("w1")
    expire_lease(jid)
    with Session(get_engine()) as s:
        assert jobs.heartbeat(s, "someone-else", [jid]) == 0
    assert row(jid).lease_expires_at < datetime.now(timezone.utc)
    with Session(get_engine()) as s:
        assert jobs.heartbeat(s, "w1", [jid]) == 1
    assert row(jid).lease_expires_at > datetime.now(timezone.utc)


def test_expired_job_is_requeued_then_failed_with_clear_message(db):
    a, _ = two_tenants(db)
    (jid,) = add(db, a)
    claim("w1")
    expire_lease(jid)
    with Session(get_engine()) as s:
        assert jobs.reap_expired(s) == {"requeued": [jid], "failed": []}
    j = row(jid)
    assert j.status == "queued" and j.worker_id is None and "stopped responding" in j.progress
    assert claim("w2") == jid and row(jid).attempts == 2
    expire_lease(jid)
    with Session(get_engine()) as s:
        assert jobs.reap_expired(s) == {"requeued": [], "failed": [jid]}
    j = row(jid)
    assert j.status == "failed" and "did not complete after 2 attempts" in j.error and j.finished_at is not None


def test_live_jobs_are_not_reaped(db):
    a, _ = two_tenants(db)
    (jid,) = add(db, a)
    claim("w1")
    with Session(get_engine()) as s:
        assert jobs.reap_expired(s) == {"requeued": [], "failed": []}
    assert row(jid).status == "running"


def test_release_returns_job_without_using_an_attempt(db):
    a, _ = two_tenants(db)
    (jid,) = add(db, a)
    claim("w1")
    with Session(get_engine()) as s:
        jobs.release(s, "w1", [jid])
    j = row(jid)
    assert j.status == "queued" and j.attempts == 0 and "restarted" in j.progress


def test_retry_or_fail_gives_up_after_max_attempts(db):
    a, _ = two_tenants(db)
    (jid,) = add(db, a)
    claim("w1")
    with Session(get_engine()) as s:
        assert jobs.retry_or_fail(s, jid, "temporary problem") == "requeued"
    assert row(jid).status == "queued" and "temporary problem" in row(jid).progress
    claim("w1")
    with Session(get_engine()) as s:
        assert jobs.retry_or_fail(s, jid, "temporary problem") == "failed"
    j = row(jid)
    assert j.status == "failed" and "temporary problem" in j.error and "2 attempts" in j.error


def test_succeed_after_being_reaped_does_not_save_anything(db):
    a, _ = two_tenants(db)
    (jid,) = add(db, a)
    claim("w1")
    expire_lease(jid)
    with Session(get_engine()) as s:
        jobs.reap_expired(s)  # the job went back to the queue while the slow worker was still going
    with Session(get_engine()) as s:
        tenant = s.scalar(select(Tenant).where(Tenant.id == a))
        from app.models import Project

        p = Project(tenant_id=tenant.id, name="P")
        s.add(p)
        s.flush()
        s.add(Requirement(tenant_id=tenant.id, project_id=p.id, job_id=jid, seq=1, summary="s", description="d", acceptance_criteria="a"))
        assert jobs.succeed(s, jid, {}) is False
    with Session(get_engine()) as s:
        assert s.scalar(select(Requirement.id)) is None  # the late worker's output was rolled back
    assert row(jid).status == "queued"


def test_metering_fields_are_saved_on_success(db):
    a, _ = two_tenants(db)
    (jid,) = add(db, a)
    claim("w1")
    with Session(get_engine()) as s:
        assert jobs.succeed(s, jid, {"n": 1}, provider="openai", model="m", tokens_in=10, tokens_out=20, duration_ms=1234)
    j = row(jid)
    assert (j.status, j.provider, j.model, j.tokens_in, j.tokens_out, j.duration_ms, j.result) == ("succeeded", "openai", "m", 10, 20, 1234, {"n": 1})


@pytest.mark.parametrize("n", [20])
def test_twenty_jobs_two_tenants_run_through_a_worker_pool(db, monkeypatch, n):
    """Queue + several workers: 20 jobs from 2 tenants all finish, none twice, caps never exceeded."""
    monkeypatch.setenv("JOB_TENANT_CONCURRENCY", "3")
    a, b = two_tenants(db)
    ids = set(add(db, a, n // 2) + add(db, b, n // 2))
    peak = {"a": 0, "b": 0}
    done, lock = [], threading.Lock()

    def worker(name):
        while True:
            jid = claim(name)
            if jid is None:
                with Session(get_engine()) as s:
                    left = s.scalar(select(text("count(*)")).select_from(Job).where(Job.status.in_(["queued", "running"])))
                if left == 0:
                    return
                continue
            with Session(get_engine()) as s:
                for key, tid in (("a", a), ("b", b)):
                    running = s.scalar(select(text("count(*)")).select_from(Job).where(Job.tenant_id == tid, Job.status == "running"))
                    with lock:
                        peak[key] = max(peak[key], running)
            with Session(get_engine()) as s:
                jobs.succeed(s, jid, {})
            with lock:
                done.append(jid)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert sorted(done) == sorted(ids) and len(done) == len(set(done))
    assert peak["a"] <= 3 and peak["b"] <= 3
