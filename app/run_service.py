"""Server side of executing a test script: what the runner is given, and saving what it reports.

The runner executes the script with Playwright Test. The server re-checks everything it is told (status, step list,
screenshots) and stores the run, its per-step results, its log and its screenshots in Postgres.
"""
import base64
import binascii
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import jobs
from app.models import Project
from app.models_runner import Runner
from app.models_tests import TsRun, TsRunArtifact, TsScript
from app.models_work import Job
from app.runner_jobs import COMPLETERS

STATUSES = ("passed", "failed", "skipped", "aborted")
MAX_STEPS = 200
MAX_LOG_CHARS = 300_000
MAX_SCREENSHOTS = 10
MAX_SCREENSHOT_BYTES = 2_000_000
MIN_RUNNER_VERSION = (0, 2)


def run_key(seq: int) -> str:
    return f"EX-{seq:03d}"


def version_ok(version: str) -> bool:
    """Runners older than 0.2 do not know how to run scripts."""
    try:
        parts = tuple(int(p) for p in str(version).split(".")[:2])
    except ValueError:
        return False
    return parts >= MIN_RUNNER_VERSION


def _clean_steps(raw) -> list:
    out = []
    for step in (raw if isinstance(raw, list) else [])[:MAX_STEPS]:
        if not isinstance(step, dict):
            continue
        out.append(
            {
                "title": str(step.get("title", ""))[:300],
                "status": "failed" if step.get("status") == "failed" else "passed",
                "duration_ms": int(step.get("duration_ms") or 0),
                "error": str(step.get("error", ""))[:4000],
            }
        )
    return out


def _clean_screenshots(raw) -> list:
    out = []
    for shot in (raw if isinstance(raw, list) else [])[:MAX_SCREENSHOTS]:
        if not isinstance(shot, dict):
            continue
        content_type = str(shot.get("content_type", ""))
        if content_type not in ("image/png", "image/jpeg"):
            continue
        try:
            data = base64.b64decode(str(shot.get("data", "")), validate=True)
        except (binascii.Error, ValueError):
            continue
        if not data or len(data) > MAX_SCREENSHOT_BYTES:
            continue
        out.append((str(shot.get("name") or "screenshot")[:80], content_type, data))
    return out


def complete_run_script(db: Session, job: Job, runner: Runner, result: dict) -> None:
    payload = job.payload
    if payload.get("batch"):  # a test batch: one job, one joined script, one result per script inside it
        from app import batch_service

        batch_service.complete_batch_run(db, job, runner, result)
        return
    script = db.get(TsScript, payload.get("script_id"))
    if script is None or script.tenant_id != job.tenant_id or script.project_id != job.project_id:
        jobs.fail(db, job.id, "The test script was deleted while it was running.")
        return
    status = str(result.get("status", "")).lower()
    if status not in STATUSES:
        status = "failed"
    duration = result.get("duration_seconds")
    # One run number sequence per project; the project row lock keeps concurrent runs from clashing.
    db.execute(select(Project.id).where(Project.id == job.project_id).with_for_update())
    base = db.scalar(select(func.coalesce(func.max(TsRun.seq), 0)).where(TsRun.project_id == job.project_id))
    run = TsRun(
        id=uuid.uuid4(),
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        script_id=script.id,
        test_case_id=script.test_case_id,
        job_id=job.id,
        runner_id=runner.id,
        seq=base + 1,
        status=status,
        exit_code=result.get("exit_code") if isinstance(result.get("exit_code"), int) else None,
        headed=bool(result.get("headed")),
        started_at=jobs.now() if not result.get("started_at") else _parse(result.get("started_at")),
        finished_at=_parse(result.get("finished_at")) or jobs.now(),
        duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
        steps=_clean_steps(result.get("steps")),
        error=str(result.get("error") or "")[:4000],
        log=str(result.get("output") or "")[-MAX_LOG_CHARS:],
        script_code=str(payload.get("code") or ""),
        data_set=payload.get("data_set") if isinstance(payload.get("data_set"), dict) else None,
        created_by=job.created_by,
    )
    db.add(run)
    db.flush()
    for name, content_type, data in _clean_screenshots(result.get("screenshots")):
        db.add(TsRunArtifact(tenant_id=job.tenant_id, run_id=run.id, name=name, content_type=content_type, data=data))
    jobs.succeed(
        db,
        job.id,
        {"run_id": str(run.id), "run_key": run_key(run.seq), "status": status, "script_id": str(script.id)},
        duration_ms=int((run.duration_seconds or 0) * 1000),
    )


def _parse(value):
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


COMPLETERS["run_script"] = complete_run_script
