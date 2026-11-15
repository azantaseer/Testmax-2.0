"""Runners: pairing (device code), admin management, and the runner-facing job API."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import jobs, runner_package, runner_target
from app.db import get_db
from app.deps import current_user, scoped_select
from app.errors import AppError
from app.models import User
from app.models_runner import PairingRequest, Runner
from app.models_work import Job
from app.routers.users import require_admin
from app.runner_auth import current_runner, hash_key, is_online, new_runner_key
from app import llm
from app.runner_jobs import COMPLETERS, RUNNER_JOB_TYPES, SYSTEM_PROMPTS, claim_payload
from app.config import get_settings

router = APIRouter(prefix="/api")

PAIRING_MINUTES = 10
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L


def _user_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _normalize_code(value: str) -> str:
    raw = "".join(c for c in value.upper() if c.isalnum())
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else ""


# ---------------------------------------------------------------- runner side: pairing
class PairStart(BaseModel):
    name: str = Field(default="", max_length=80)
    capacity: int = Field(default=2, ge=1, le=20)
    os: str = Field(default="", max_length=80)
    version: str = Field(default="", max_length=40)


@router.post("/runner/pair/start", status_code=201)
def pair_start(body: PairStart, db: Session = Depends(get_db)):
    device_code = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.execute(text("delete from pairing_requests where expires_at < now() - interval '1 hour'"))
    for _ in range(10):
        code = _user_code()
        if not db.scalar(select(PairingRequest.id).where(PairingRequest.user_code == code, PairingRequest.status == "pending")):
            break
    db.add(
        PairingRequest(
            device_code_hash=hashlib.sha256(device_code.encode()).hexdigest(),
            user_code=code,
            requested_name=body.name.strip(),
            capacity=body.capacity,
            info={"os": body.os, "version": body.version},
            expires_at=now + timedelta(minutes=PAIRING_MINUTES),
        )
    )
    db.commit()
    return {"device_code": device_code, "user_code": code, "expires_in": PAIRING_MINUTES * 60, "interval": 3}


class PairPoll(BaseModel):
    device_code: str


@router.post("/runner/pair/poll")
def pair_poll(body: PairPoll, db: Session = Depends(get_db)):
    req = db.scalar(select(PairingRequest).where(PairingRequest.device_code_hash == hashlib.sha256(body.device_code.encode()).hexdigest()))
    if req is None:
        raise AppError(404, "pairing_not_found", "Unknown pairing request.")
    if req.status == "consumed":
        raise AppError(410, "pairing_used", "This pairing was already completed.")
    if req.expires_at < datetime.now(timezone.utc) and req.status == "pending":
        raise AppError(410, "pairing_expired", "The pairing code expired. Start again.")
    if req.status == "pending":
        return {"status": "pending"}
    key, runner_id = req.pending_key, req.runner_id
    req.pending_key = None  # the key leaves the database here; only its hash remains
    req.status = "consumed"
    db.commit()
    return {"status": "approved", "runner_key": key, "runner_id": str(runner_id)}


# ---------------------------------------------------------------- admin side
class Approve(BaseModel):
    user_code: str
    name: str = Field(default="", max_length=80)


def _runner_dict(r: Runner, running: int = 0) -> dict:
    return {
        "id": str(r.id),
        "name": r.name,
        "capacity": r.capacity,
        "online": r.revoked_at is None and is_online(r),
        "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        "created_at": r.created_at.isoformat(),
        "info": r.info,
        "running_jobs": running,
    }


@router.post("/runners/pair", status_code=201)
def approve_pairing(body: Approve, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    code = _normalize_code(body.user_code)
    req = db.scalar(select(PairingRequest).where(PairingRequest.user_code == code, PairingRequest.status == "pending")) if code else None
    if req is None or req.expires_at < datetime.now(timezone.utc):
        raise AppError(404, "code_not_found", "That code was not found or has expired. Check the code shown by the runner.")
    name = (body.name.strip() or req.requested_name or "Runner")[:80]
    key = new_runner_key()
    runner = Runner(tenant_id=admin.tenant_id, name=name, key_hash=hash_key(key), capacity=req.capacity, info=req.info, created_by=admin.id)
    db.add(runner)
    db.flush()
    req.status, req.tenant_id, req.runner_id, req.pending_key, req.approved_by = "approved", admin.tenant_id, runner.id, key, admin.id
    db.commit()
    return _runner_dict(runner)


@router.get("/runners")
def list_runners(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    runners = db.scalars(scoped_select(Runner, admin).where(Runner.revoked_at.is_(None)).order_by(func.lower(Runner.name))).all()
    counts = dict(
        db.execute(
            select(Job.runner_id, func.count()).where(Job.tenant_id == admin.tenant_id, Job.status == "running", Job.runner_id.is_not(None)).group_by(Job.runner_id)
        ).all()
    )
    return {"runners": [_runner_dict(r, counts.get(r.id, 0)) for r in runners]}


@router.get("/runners/package")
def download_runner_package(
    request: Request,
    system: str = Query(default="windows"),
    server: str = Query(default=""),
    admin: User = Depends(require_admin),
):
    """The runner as a zip with the server address filled in (no secrets inside; the runner pairs with a code)."""
    try:
        data = runner_package.build(system, server or str(request.base_url))
    except runner_package.PackageError as err:
        raise AppError(422, "bad_package_request", str(err)) from None
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="testmax-runner-{system}.zip"', "Cache-Control": "no-store"},
    )


@router.get("/runners/status")
def runner_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Any signed-in user: is at least one runner of the organization online? (used by the Scan dialog)"""
    runners = db.scalars(scoped_select(Runner, user).where(Runner.revoked_at.is_(None)).order_by(func.lower(Runner.name))).all()
    from app import run_service

    return {
        "total": len(runners),
        "online": sum(1 for r in runners if is_online(r)),
        # for the "Run on" choice in the Scan and Execute dialogs
        "runners": [
            {"id": str(r.id), "name": r.name, "online": is_online(r), "capacity": r.capacity, "version": (r.info or {}).get("version", ""),
             "can_run_scripts": run_service.version_ok((r.info or {}).get("version", ""))}
            for r in runners
        ],
    }


@router.get("/runners/limits")
def runner_limits(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """How many jobs of the organization may run at the same time, and how many do right now."""
    return runner_target.limits_view(db, user)


@router.put("/runners/limits")
def change_runner_limits(body: runner_target.LimitEdit, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return runner_target.set_limit(db, admin, body.limit)


@router.delete("/runners/{runner_id}", status_code=204)
def revoke_runner(runner_id: uuid.UUID, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    runner = db.scalar(scoped_select(Runner, admin).where(Runner.id == runner_id, Runner.revoked_at.is_(None)))
    if runner is None:
        raise AppError(404, "runner_not_found", "Runner not found.")
    runner.revoked_at = datetime.now(timezone.utc)
    runner_target.fail_waiting_jobs(db, runner)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- runner side: heartbeat, claim, job lifecycle
class Heartbeat(BaseModel):
    capacity: int | None = Field(default=None, ge=1, le=20)
    os: str = Field(default="", max_length=80)
    version: str = Field(default="", max_length=40)


@router.post("/runner/heartbeat")
def runner_heartbeat(body: Heartbeat, runner: Runner = Depends(current_runner), db: Session = Depends(get_db)):
    if body.capacity:
        runner.capacity = body.capacity
    if body.os or body.version:
        runner.info = {**runner.info, "os": body.os or runner.info.get("os", ""), "version": body.version or runner.info.get("version", "")}
    db.commit()
    return {"ok": True, "tenant_id": str(runner.tenant_id), "runner_id": str(runner.id)}


class Claim(BaseModel):
    free_slots: int = Field(default=1, ge=0, le=20)


@router.post("/runner/claim")
def runner_claim(body: Claim, runner: Runner = Depends(current_runner), db: Session = Depends(get_db)):
    if body.free_slots < 1:
        return {"job": None}
    job_id = jobs.claim(db, f"runner:{runner.id}", RUNNER_JOB_TYPES, tenant_id=runner.tenant_id, runner_id=runner.id)
    if job_id is None:
        return {"job": None}
    job = db.get(Job, job_id)
    return {"job": claim_payload(job)}


def _own_running_job(db: Session, runner: Runner, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.tenant_id != runner.tenant_id or job.runner_id != runner.id or job.status != "running":
        raise AppError(409, "not_your_job", "This job is not running on this runner.")
    return job


@router.post("/runner/jobs/{job_id}/heartbeat")
def job_heartbeat(job_id: uuid.UUID, runner: Runner = Depends(current_runner), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None or job.runner_id != runner.id or job.tenant_id != runner.tenant_id:
        raise AppError(404, "job_not_found", "Job not found.")
    renewed = jobs.heartbeat(db, f"runner:{runner.id}", [job_id]) > 0
    db.refresh(job)
    return {"ok": renewed, "status": job.status, "cancel_requested": bool(job.cancel_requested)}


class Progress(BaseModel):
    message: str = Field(max_length=300)


@router.post("/runner/jobs/{job_id}/progress", status_code=204)
def job_progress(job_id: uuid.UUID, body: Progress, runner: Runner = Depends(current_runner), db: Session = Depends(get_db)):
    _own_running_job(db, runner, job_id)
    jobs.set_progress(db, job_id, body.message)
    return Response(status_code=204)


class Complete(BaseModel):
    result: dict = {}


@router.post("/runner/jobs/{job_id}/complete")
def job_complete(job_id: uuid.UUID, body: Complete, runner: Runner = Depends(current_runner), db: Session = Depends(get_db)):
    job = _own_running_job(db, runner, job_id)
    completer = COMPLETERS.get(job.type)
    if completer is None:
        raise AppError(400, "unknown_job_type", "Unknown job type.")
    completer(db, job, runner, body.result)
    db.refresh(job)
    return {"status": job.status}


class Fail(BaseModel):
    message: str = Field(max_length=1000)
    retryable: bool = False


@router.post("/runner/jobs/{job_id}/fail")
def job_fail(job_id: uuid.UUID, body: Fail, runner: Runner = Depends(current_runner), db: Session = Depends(get_db)):
    _own_running_job(db, runner, job_id)
    state = jobs.retry_or_fail(db, job_id, body.message) if body.retryable else (jobs.fail(db, job_id, body.message) or "failed")
    return {"state": state}


# ---------------------------------------------------------------- runner side: AI proxy
MAX_PROXY_BYTES = 6 * 1024 * 1024


class LLMRequest(BaseModel):
    job_id: uuid.UUID
    messages: list[dict]
    tools: list[dict] = []
    tool_choice: str | None = None


@router.post("/runner/llm")
def runner_llm(body: LLMRequest, runner: Runner = Depends(current_runner), db: Session = Depends(get_db)):
    """The runner holds no AI key: every model call goes through here.

    Which model answers is decided by the job (fixed when the user started it), the system prompt comes from the
    server, and only a job this runner is running right now may use it.
    """
    import json

    job = _own_running_job(db, runner, body.job_id)
    system = SYSTEM_PROMPTS.get(job.type)
    if system is None:
        raise AppError(400, "proxy_not_allowed", "This job type does not use the AI proxy.")
    if len(json.dumps(body.messages)) > MAX_PROXY_BYTES:
        raise AppError(413, "too_large", "The conversation is too large.")
    used = (job.tokens_in or 0) + (job.tokens_out or 0)
    if used > get_settings().job_max_tokens:
        raise AppError(429, "token_budget_exceeded", "This job used up its AI token budget.")
    provider, model = job.payload.get("provider", ""), job.payload.get("model", "")
    try:
        result = llm.chat(provider, model, system(job), body.messages, body.tools, tool_choice=body.tool_choice)
    except llm.LLMError as err:
        raise AppError(503 if err.retryable else 502, "llm_retryable" if err.retryable else "llm_failed", err.message) from None
    jobs.add_usage(db, job.id, result.tokens_in, result.tokens_out)
    for note in result.notes:
        jobs.add_note(db, job.id, note)
    return {"text": result.text, "tool_calls": result.tool_calls, "tokens_in": result.tokens_in, "tokens_out": result.tokens_out}
