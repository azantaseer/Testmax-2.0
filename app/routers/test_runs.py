"""Execute a test script on a runner, run history and the execution report. Tenant-scoped."""
import uuid

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import jobs, runner_target
from app.db import get_db
from app.deps import current_user, scoped_select
from app.errors import AppError
from app.models import User
from app.models_runner import Runner
from app.models_tests import TcCase, TsRun, TsRunArtifact, TsScript
from app.models_work import Job
from app.routers.projects import get_project
from app.routers.requirements import _job_dict
from app.run_service import run_key, version_ok
from app.runner_auth import is_online

router = APIRouter(prefix="/api")

JOB_TYPE = "run_script"
RECENT = 10


def _run_row(r: TsRun, extra: dict | None = None) -> dict:
    return {
        "id": str(r.id),
        "key": run_key(r.seq),
        "script_id": str(r.script_id),
        "status": r.status,
        "duration_seconds": r.duration_seconds,
        "headed": r.headed,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "created_at": r.created_at.isoformat(),
        **(extra or {}),
    }


def last_runs(db: Session, user: User, script_ids: list) -> dict:
    """script id -> its most recent run (for the scripts list)."""
    if not script_ids:
        return {}
    rows = db.scalars(scoped_select(TsRun, user).where(TsRun.script_id.in_(script_ids)).order_by(TsRun.created_at.desc())).all()
    out: dict = {}
    for r in rows:
        out.setdefault(r.script_id, _run_row(r))
    return out


def run_stats(db: Session, user: User, script_id: uuid.UUID) -> dict:
    """Run history numbers for the script viewer. Aborted runs count as runs but not in the pass rate or the average."""
    rows = db.scalars(scoped_select(TsRun, user).where(TsRun.script_id == script_id).order_by(TsRun.created_at.desc())).all()
    finished = [r for r in rows if r.status != "aborted"]
    passed = [r for r in finished if r.status == "passed"]
    durations = [r.duration_seconds for r in finished if r.duration_seconds is not None]
    return {
        "total": len(rows),
        "passed": len(passed),
        "pass_rate": round(100 * len(passed) / len(finished)) if finished else None,
        "avg_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        "last_run_at": rows[0].created_at.isoformat() if rows else None,
        "recent": [_run_row(r) for r in rows[:RECENT]],
    }


def _get_script(db: Session, user: User, project_id: uuid.UUID, script_id: uuid.UUID) -> TsScript:
    s = db.scalar(scoped_select(TsScript, user).where(TsScript.id == script_id, TsScript.project_id == project_id))
    if s is None:
        raise AppError(404, "test_script_not_found", "Test script not found.")
    return s


class ExecuteRequest(BaseModel):
    runner_id: uuid.UUID | None = None  # None = any online runner of the organization


@router.post("/projects/{project_id}/test-scripts/{script_id}/execute", status_code=202)
def execute_script(project_id: uuid.UUID, script_id: uuid.UUID, body: ExecuteRequest | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = get_project(db, user, project_id)
    script = _get_script(db, user, project.id, script_id)
    case = db.scalar(scoped_select(TcCase, user).where(TcCase.id == script.test_case_id))
    if script.blocked_message:
        # Pilot 1 does not open a browser for a script that would stop at once: say why instead.
        raise AppError(409, "needs_test_data", script.blocked_message)
    running = db.scalar(
        scoped_select(Job, user).where(
            Job.project_id == project.id, Job.type == JOB_TYPE, Job.status.in_(("queued", "running")), Job.payload["script_id"].astext == str(script.id)
        )
    )
    if running is not None:
        raise AppError(409, "already_running", "This script is already being executed.")
    target = runner_target.resolve_target(db, user, body.runner_id if body else None, needs_scripts=True)
    runners = [r for r in db.scalars(scoped_select(Runner, user).where(Runner.revoked_at.is_(None))).all() if is_online(r)]
    if not runners:
        raise AppError(409, "no_runner_online", "No runner is online for your organization. Connect a runner (see the Runners page), then try again.")
    if not any(version_ok((r.info or {}).get("version", "")) for r in runners):
        raise AppError(
            409,
            "runner_outdated",
            "Your runner is too old to execute scripts. Stop it and start it again from the updated TestMax folder (python -m runner run), then try again.",
        )
    key = f"TC-{case.seq:03d}" if case else "script"
    job = jobs.enqueue(
        db,
        tenant_id=user.tenant_id,
        job_type=JOB_TYPE,
        project_id=project.id,
        created_by=user.id,
        max_attempts=1,  # a run is never repeated on its own: it may have changed data in the application under test
        target_runner_id=target.id if target else None,
        payload={
            **runner_target.target_payload(target),
            "script_id": str(script.id),
            "script_key": f"TS-{script.seq:03d}",
            "test_case_id": str(script.test_case_id),
            "file_name": f"{key}.spec.js",
            "code": script.code,
            "filename": f"TS-{script.seq:03d}",
        },
    )
    return {**_job_dict(job), "script_id": str(script.id)}


@router.get("/projects/{project_id}/test-scripts/{script_id}/runs")
def script_runs(project_id: uuid.UUID, script_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    _get_script(db, user, project_id, script_id)
    return run_stats(db, user, script_id)


def _get_run(db: Session, user: User, project_id: uuid.UUID, run_id: uuid.UUID) -> TsRun:
    r = db.scalar(scoped_select(TsRun, user).where(TsRun.id == run_id, TsRun.project_id == project_id))
    if r is None:
        raise AppError(404, "test_run_not_found", "Execution not found.")
    return r


@router.get("/projects/{project_id}/test-runs/{run_id}")
def read_run(project_id: uuid.UUID, run_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    r = _get_run(db, user, project_id, run_id)
    script = db.scalar(scoped_select(TsScript, user).where(TsScript.id == r.script_id))
    case = db.scalar(scoped_select(TcCase, user).where(TcCase.id == r.test_case_id))
    runner = db.get(Runner, r.runner_id) if r.runner_id else None
    runner_name = runner.name if runner and runner.tenant_id == user.tenant_id else None
    executed_by = db.get(User, r.created_by) if r.created_by else None
    artifacts = db.execute(
        select(TsRunArtifact.id, TsRunArtifact.name, TsRunArtifact.content_type, func.length(TsRunArtifact.data))
        .where(TsRunArtifact.run_id == r.id, TsRunArtifact.tenant_id == user.tenant_id)
        .order_by(TsRunArtifact.created_at)
    ).all()
    return {
        **_run_row(r),
        "project_id": str(r.project_id),
        "exit_code": r.exit_code,
        "steps": r.steps,
        "error": r.error,
        "log": r.log,
        "script": {"id": str(script.id), "key": f"TS-{script.seq:03d}"} if script else None,
        "test_case": {"id": str(case.id), "key": f"TC-{case.seq:03d}", "title": case.title, "type": case.case_type, "step_count": len(case.steps or [])} if case else None,
        "runner": runner_name,
        "executed_by": executed_by.name if executed_by and executed_by.tenant_id == user.tenant_id else None,
        "artifacts": [{"id": str(a[0]), "name": a[1], "content_type": a[2], "size": a[3]} for a in artifacts],
    }


@router.get("/projects/{project_id}/test-runs/{run_id}/artifacts/{artifact_id}")
def read_artifact(project_id: uuid.UUID, run_id: uuid.UUID, artifact_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    _get_run(db, user, project_id, run_id)
    a = db.scalar(scoped_select(TsRunArtifact, user).where(TsRunArtifact.id == artifact_id, TsRunArtifact.run_id == run_id))
    if a is None:
        raise AppError(404, "artifact_not_found", "Screenshot not found.")
    return Response(a.data, media_type=a.content_type, headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})
