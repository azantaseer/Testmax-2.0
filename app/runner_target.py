"""Choosing which runner takes a job, and how many jobs an organization may run at the same time.

- A job normally goes to whichever runner of the organization asks first. A person may instead pick ONE runner for a
  scan or an execution; then only that runner can take the job (the claim query checks `target_runner_id`).
- The picked runner must belong to the caller's organization, not be revoked, be online right now and (for scripts) be
  new enough. If it goes away while the job is still waiting, the job says so and can be cancelled; if an administrator
  revokes it, its waiting jobs are failed at once (they could never run).
- The number of jobs of one organization that run at the same time (all runners and the server's AI jobs together) is a
  per-organization setting; without one the system default applies (JOB_TENANT_CONCURRENCY, 3).
"""
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import run_service
from app.config import get_settings
from app.deps import scoped_select
from app.errors import AppError
from app.models import Tenant, User
from app.models_runner import Runner
from app.models_work import Job
from app.runner_auth import is_online

ACTIVE = ("queued", "running")


def resolve_target(db: Session, user: User, runner_id: uuid.UUID | None, *, needs_scripts: bool = False) -> Runner | None:
    """The chosen runner, or None for "any runner". Refuses a runner that cannot take the job right now."""
    if runner_id is None:
        return None
    runner = db.scalar(scoped_select(Runner, user).where(Runner.id == runner_id))
    if runner is None or runner.revoked_at is not None:
        raise AppError(404, "runner_not_found", "That runner no longer exists. Choose another runner, or Any runner.")
    if not is_online(runner):
        raise AppError(409, "runner_offline", f'The runner "{runner.name}" is offline. Start it, or choose another runner, or Any runner.')
    if needs_scripts and not run_service.version_ok((runner.info or {}).get("version", "")):
        raise AppError(409, "runner_outdated", f'The runner "{runner.name}" is too old to execute scripts. Start it from the updated TestMax folder, or choose another runner.')
    return runner


def target_payload(runner: Runner | None) -> dict:
    """What the job remembers about its chosen runner (for the screens)."""
    return {"target_runner": {"id": str(runner.id), "name": runner.name}} if runner else {}


def fail_waiting_jobs(db: Session, runner: Runner) -> int:
    """A revoked runner will never come back: jobs that were waiting for it only could run on it."""
    res = db.execute(
        update(Job)
        .where(Job.tenant_id == runner.tenant_id, Job.target_runner_id == runner.id, Job.status == "queued")
        .values(status="failed", error=f'The runner "{runner.name}" was removed before it could take this job. Start it again and choose another runner.', progress="Failed", finished_at=func.now())
    )
    return res.rowcount or 0


# ---------------------------------------------------------------- the per-organization limit
class LimitEdit(BaseModel):
    limit: int | None = Field(default=None, description="null = back to the system default")


def default_limit() -> int:
    return get_settings().job_tenant_concurrency


def max_limit() -> int:
    return get_settings().job_tenant_concurrency_max


def effective_limit(tenant: Tenant) -> int:
    return tenant.job_concurrency or default_limit()


def limits_view(db: Session, user: User) -> dict:
    tenant = db.get(Tenant, user.tenant_id)
    runners = db.scalars(scoped_select(Runner, user).where(Runner.revoked_at.is_(None))).all()
    online = [r for r in runners if is_online(r)]
    running = db.scalar(select(func.count()).select_from(Job).where(Job.tenant_id == user.tenant_id, Job.status == "running")) or 0
    limit = effective_limit(tenant)
    return {
        "limit": limit,
        "default": default_limit(),
        "max": max_limit(),
        "custom": tenant.job_concurrency is not None,
        "running": running,
        "runners_online": len(online),
        "runner_slots": sum(r.capacity for r in online),
    }


def set_limit(db: Session, user: User, value: int | None) -> dict:
    if value is not None and not 1 <= value <= max_limit():
        raise AppError(422, "bad_limit", f"Enter a number from 1 to {max_limit()}.")
    tenant = db.get(Tenant, user.tenant_id)
    tenant.job_concurrency = None if value is None or value == default_limit() else value
    db.commit()
    return limits_view(db, user)
