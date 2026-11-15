"""The Postgres job queue.

One `jobs` table, claimed with SELECT ... FOR UPDATE SKIP LOCKED.
- Per-tenant concurrency cap (the organization's own setting, else the system default) and fairness: among queued jobs, the tenant with the fewest running jobs goes first.
- A running job holds a lease that its worker renews (heartbeat). A job whose lease expired is requeued, or failed
  with a clear message once it used all its attempts, so nothing stays "running" forever.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models_work import Job

# Serialises claims so the per-tenant cap is exact. Claims are tiny, so this costs nothing measurable.
_CLAIM_LOCK = 7_070_001


def enqueue(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    job_type: str,
    payload: dict,
    project_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    max_attempts: int | None = None,
    target_runner_id: uuid.UUID | None = None,
) -> Job:
    job = Job(
        tenant_id=tenant_id,
        project_id=project_id,
        type=job_type,
        payload=payload,
        created_by=created_by,
        target_runner_id=target_runner_id,
        max_attempts=max_attempts or get_settings().job_max_attempts,
        progress="Waiting in the queue",
    )
    db.add(job)
    db.commit()
    return job


_CLAIM_SQL = text(
    """
    with running as (
        select tenant_id, count(*) as n from jobs where status = 'running' group by tenant_id
    ),
    candidate as (
        select j.id
        from jobs j
        join tenants t on t.id = j.tenant_id
        left join running r on r.tenant_id = j.tenant_id
        where j.status = 'queued'
          and coalesce(r.n, 0) < coalesce(t.job_concurrency, :cap)
          and (j.target_runner_id is null or j.target_runner_id = cast(:runner as uuid))
          and (cast(:types as text[]) is null or j.type = any(cast(:types as text[])))
          and (cast(:tenant as uuid) is null or j.tenant_id = cast(:tenant as uuid))
        order by coalesce(r.n, 0), j.created_at
        for update of j skip locked
        limit 1
    )
    update jobs
       set status = 'running',
           worker_id = :worker,
           runner_id = cast(:runner as uuid),
           cancel_requested = false,
           attempts = attempts + 1,
           started_at = coalesce(started_at, now()),
           lease_expires_at = now() + make_interval(secs => :lease),
           progress = 'Starting'
     where id in (select id from candidate)
    returning id
    """
)


def claim(
    db: Session,
    worker_id: str,
    types: list[str] | None = None,
    *,
    tenant_id: uuid.UUID | None = None,
    runner_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    s = get_settings()
    db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": _CLAIM_LOCK})
    row = db.execute(
        _CLAIM_SQL,
        {
            "cap": s.job_tenant_concurrency,
            "worker": worker_id,
            "lease": s.job_lease_seconds,
            "types": types,
            "tenant": str(tenant_id) if tenant_id else None,
            "runner": str(runner_id) if runner_id else None,
        },
    ).first()
    db.commit()
    return row[0] if row else None


def heartbeat(db: Session, worker_id: str, job_ids: list[uuid.UUID]) -> int:
    """Extend the lease of the jobs this worker still runs."""
    if not job_ids:
        return 0
    res = db.execute(
        text(
            "update jobs set lease_expires_at = now() + make_interval(secs => :lease) "
            "where status = 'running' and worker_id = :w and id = any(:ids)"
        ),
        {"lease": get_settings().job_lease_seconds, "w": worker_id, "ids": job_ids},
    )
    db.commit()
    return res.rowcount


def request_cancel(db: Session, job_id: uuid.UUID) -> str:
    """Queued jobs are cancelled at once; a running job is told to stop (the runner sees it on its next heartbeat)."""
    res = db.execute(
        text(
            "update jobs set status='failed', error='Cancelled by the user.', progress='Cancelled', finished_at=now(), "
            "lease_expires_at=null where id=:id and status='queued'"
        ),
        {"id": job_id},
    )
    if res.rowcount:
        db.commit()
        return "cancelled"
    res = db.execute(text("update jobs set cancel_requested=true where id=:id and status='running'"), {"id": job_id})
    db.commit()
    return "cancelling" if res.rowcount else "finished"


def add_usage(db: Session, job_id: uuid.UUID, tokens_in: int | None, tokens_out: int | None) -> None:
    """Metering for jobs that make several model calls: running totals on the job."""
    db.execute(
        text("update jobs set tokens_in = coalesce(tokens_in,0) + :i, tokens_out = coalesce(tokens_out,0) + :o where id=:id"),
        {"i": tokens_in or 0, "o": tokens_out or 0, "id": job_id},
    )
    db.commit()


def add_note(db: Session, job_id: uuid.UUID, note: str) -> None:
    """Remember something the system did on its own for this job (shown with the result). Each note once."""
    db.execute(
        text(
            "update jobs set payload = jsonb_set(payload, '{notes}', coalesce(payload->'notes', '[]'::jsonb) || jsonb_build_array(cast(:n as text))) "
            "where id=:id and not (coalesce(payload->'notes', '[]'::jsonb) @> jsonb_build_array(cast(:n as text)))"
        ),
        {"n": note[:300], "id": job_id},
    )
    db.commit()


def set_progress(db: Session, job_id: uuid.UUID, message: str) -> None:
    db.execute(text("update jobs set progress = :m where id = :id and status = 'running'"), {"m": message[:300], "id": job_id})
    db.commit()


def succeed(db: Session, job_id: uuid.UUID, result: dict, *, provider=None, model=None, tokens_in=None, tokens_out=None, duration_ms=None) -> bool:
    """Mark done and commit everything the handler added. If the job is no longer ours (it was reaped), roll back."""
    res = db.execute(
        text(
            "update jobs set status='succeeded', result=cast(:r as jsonb), error=null, progress='Done', finished_at=now(), "
            "lease_expires_at=null, provider=coalesce(:p, provider), model=coalesce(:m, model), tokens_in=coalesce(:ti, tokens_in), "
            "tokens_out=coalesce(:to, tokens_out), duration_ms=coalesce(:d, duration_ms) "
            "where id=:id and status='running'"
        ),
        {"r": _json(result), "p": provider, "m": model, "ti": tokens_in, "to": tokens_out, "d": duration_ms, "id": job_id},
    )
    if res.rowcount == 0:
        db.rollback()
        return False
    db.commit()
    return True


def fail(db: Session, job_id: uuid.UUID, message: str, result: dict | None = None) -> None:
    db.execute(
        text(
            "update jobs set status='failed', error=:e, progress='Failed', finished_at=now(), lease_expires_at=null, "
            "result=coalesce(cast(:r as jsonb), result) where id=:id and status in ('running','queued')"
        ),
        {"e": message[:1000], "r": _json(result) if result is not None else None, "id": job_id},
    )
    db.commit()


def retry_or_fail(db: Session, job_id: uuid.UUID, message: str) -> str:
    """A retryable problem: back to the queue if attempts remain, otherwise fail with the message."""
    job = db.get(Job, job_id)
    db.refresh(job)
    if job.attempts < job.max_attempts:
        db.execute(
            text(
                "update jobs set status='queued', worker_id=null, lease_expires_at=null, "
                "progress=:p where id=:id and status='running'"
            ),
            {"p": f"Retrying (attempt {job.attempts + 1} of {job.max_attempts}): {message}"[:300], "id": job_id},
        )
        db.commit()
        return "requeued"
    fail(db, job_id, f"{message} (gave up after {job.attempts} attempts)")
    return "failed"


def release(db: Session, worker_id: str, job_ids: list[uuid.UUID]) -> None:
    """Clean shutdown: give unfinished jobs back at once, without using up an attempt."""
    if not job_ids:
        return
    db.execute(
        text(
            "update jobs set status='queued', worker_id=null, lease_expires_at=null, attempts=greatest(attempts-1,0), "
            "progress='The worker was restarted; the job was put back in the queue' "
            "where status='running' and worker_id=:w and id = any(:ids)"
        ),
        {"w": worker_id, "ids": job_ids},
    )
    db.commit()


def reap_expired(db: Session) -> dict:
    """Recover jobs whose worker stopped renewing the lease (crashed, killed, or lost)."""
    db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": _CLAIM_LOCK})
    requeued = db.execute(
        text(
            "update jobs set status='queued', worker_id=null, lease_expires_at=null, "
            "progress='The worker or runner stopped responding; the job was put back in the queue' "
            "where status='running' and lease_expires_at < now() and attempts < max_attempts returning id"
        )
    ).fetchall()
    failed = db.execute(
        text(
            "update jobs set status='failed', finished_at=now(), lease_expires_at=null, progress='Failed', "
            "error='The worker or runner running this job stopped responding and the job did not complete after ' || attempts || ' attempts. Please start it again.' "
            "where status='running' and lease_expires_at < now() and attempts >= max_attempts returning id"
        )
    ).fetchall()
    db.commit()
    return {"requeued": [r[0] for r in requeued], "failed": [r[0] for r in failed]}


def _json(value: dict) -> str:
    import json

    return json.dumps(value)


def now() -> datetime:
    return datetime.now(timezone.utc)


def load(db: Session, job_id: uuid.UUID) -> Job | None:
    return db.scalar(select(Job).where(Job.id == job_id))
