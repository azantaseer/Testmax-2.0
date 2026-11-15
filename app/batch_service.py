"""Server side of running a test batch: what the runner is given, and saving what it reports.

A batch run is an ordinary `run_script` job whose payload carries a `batch` block, so the runner program is not changed: it gets
one joined Playwright test file (see app/engines/batch.py) and reports its steps. Here the step list is cut into one result per
script; each script that ran also becomes a normal execution (TsRun) of that script, linked to the batch run.
"""
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import jobs
from app.engines import batch as batch_engine
from app.models import Project
from app.models_runner import Runner
from app.models_tests import TbBatch, TbRun, TsRun, TsRunArtifact, TsScript
from app.models_work import Job
from app.run_service import STATUSES, _clean_screenshots, _clean_steps, _parse, MAX_LOG_CHARS

BATCH_RESULT_STATUSES = ("passed", "failed", "not_run", "aborted")


def batch_key(seq: int) -> str:
    return f"TB-{seq:03d}"


def batch_run_key(seq: int) -> str:
    return f"BR-{seq:03d}"


def _item_status(part: dict, run_status: str) -> str:
    if not part["started"]:
        return "not_run"
    if part["done"] and not part["failed"] and not any(s["status"] == "failed" for s in part["steps"]):
        return "passed"
    if run_status == "aborted" and not part["done"] and not part["failed"]:
        return "aborted"
    return "failed"


def _item_error(part: dict, status: str, result_error: str) -> str:
    if status in ("passed", "not_run"):
        return ""
    if part["error"]:
        return part["error"]
    failed = next((s for s in part["steps"] if s["status"] == "failed"), None)
    if failed and failed.get("error"):
        return failed["error"]
    if status == "aborted":
        return "The run was stopped while this script was running."
    return result_error or "The run ended while this script was running."


def overall_status(run_status: str, item_statuses: list) -> str:
    if run_status == "aborted":
        return "aborted"
    if run_status == "skipped":
        return "skipped"
    if any(s != "passed" for s in item_statuses):
        return "failed"
    return "passed" if run_status == "passed" else "failed"


def complete_batch_run(db: Session, job: Job, runner: Runner, result: dict) -> None:
    payload = job.payload
    block = payload.get("batch") or {}
    items = block.get("items") or []
    run_status = str(result.get("status", "")).lower()
    if run_status not in STATUSES:
        run_status = "failed"
    result_error = str(result.get("error") or "")[:4000]
    steps = _clean_steps(result.get("steps"))
    if not any(batch_engine.parse_marker(s["title"]) for s in steps):  # no report from Playwright (cancelled or killed): read the console output
        steps = batch_engine.markers_from_output(result.get("output"))
    parts = batch_engine.split_steps(steps, len(items))
    statuses = {item["position"]: _item_status(parts[item["position"]], run_status) for item in items}
    status = overall_status(run_status, list(statuses.values()))
    started_at = _parse(result.get("started_at")) or jobs.now()
    duration = result.get("duration_seconds")
    # One batch run number sequence and one execution number sequence per project; the project row lock keeps concurrent runs apart.
    db.execute(select(Project.id).where(Project.id == job.project_id).with_for_update())
    batch_seq = db.scalar(select(func.coalesce(func.max(TbRun.seq), 0)).where(TbRun.project_id == job.project_id)) + 1
    run_seq = db.scalar(select(func.coalesce(func.max(TsRun.seq), 0)).where(TsRun.project_id == job.project_id))
    try:
        batch_id = uuid.UUID(str(block.get("batch_id")))
    except ValueError:
        batch_id = None
    if batch_id is not None and db.get(TbBatch, batch_id) is None:
        batch_id = None  # the batch was deleted while it ran; the run is kept only through the executions below
    record = TbRun(
        id=uuid.uuid4(),
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        batch_id=batch_id,
        job_id=job.id,
        runner_id=runner.id,
        seq=batch_seq,
        batch_key=str(block.get("key") or ""),
        batch_name=str(block.get("name") or "")[:200],
        on_failure=str(block.get("on_failure") or "stop"),
        status=status,
        headed=bool(result.get("headed")),
        started_at=started_at,
        finished_at=_parse(result.get("finished_at")) or jobs.now(),
        duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
        items=[],
        error=result_error,
        log=str(result.get("output") or "")[-MAX_LOG_CHARS:],
        created_by=job.created_by,
    )
    db.add(record)
    db.flush()
    shots = {}
    for name, content_type, data in _clean_screenshots(result.get("screenshots")):
        match = batch_engine.SHOT_NAME.match(name)
        if match:
            shots.setdefault(int(match.group(1)), (content_type, data))
    snapshot = []
    offset = 0.0
    for item in items:
        n = item["position"]
        part, item_status = parts[n], statuses[n]
        error = _item_error(part, item_status, result_error)
        seconds = round(part["duration_ms"] / 1000, 2) if part["duration_ms"] is not None else None
        script = db.get(TsScript, item.get("script_id"))
        if script is not None and (script.tenant_id != job.tenant_id or script.project_id != job.project_id):
            script = None
        row = {
            "position": n,
            "script_id": item.get("script_id") if script is not None else None,
            "script_key": item.get("script_key"),
            "test_case_id": item.get("test_case_id") if script is not None else None,
            "test_case_key": item.get("test_case_key"),
            "title": item.get("title"),
            "data_set": ({"id": item["data_set"]["id"], "name": item["data_set"]["name"]} if item.get("data_set") else None),
            "status": item_status,
            "duration_seconds": seconds,
            "error": error[:1500],
            "steps_total": len(part["steps"]),
            "steps_failed": sum(1 for s in part["steps"] if s["status"] == "failed"),
            "run_id": None,
            "run_key": None,
        }
        if item_status != "not_run" and script is not None:
            own_steps = list(part["steps"])
            if item_status == "failed" and error and not any(s["status"] == "failed" for s in own_steps):
                own_steps.append({"title": "Script error", "status": "failed", "duration_ms": 0, "error": error[:4000]})
            run_seq += 1
            start = started_at + timedelta(seconds=offset)
            run = TsRun(
                id=uuid.uuid4(),
                tenant_id=job.tenant_id,
                project_id=job.project_id,
                script_id=script.id,
                test_case_id=script.test_case_id,
                job_id=job.id,
                runner_id=runner.id,
                seq=run_seq,
                status=item_status,
                headed=bool(result.get("headed")),
                started_at=start,
                finished_at=start + timedelta(seconds=seconds) if seconds is not None else None,
                duration_seconds=seconds,
                steps=own_steps,
                error=error[:4000],
                log=(
                    f"Ran as script {n} of {len(items)} in batch {record.batch_key} ({record.batch_name}), batch run {batch_run_key(record.seq)}.\n"
                    "The console output of the whole batch is on the batch run page."
                    + (f"\n\n{error[:4000]}" if error else "")
                ),
                script_code=str(item.get("code") or ""),
                data_set=item.get("data_set") if isinstance(item.get("data_set"), dict) else None,
                batch_run_id=record.id,
                batch_position=n,
                created_by=job.created_by,
            )
            db.add(run)
            db.flush()
            if n in shots:
                db.add(TsRunArtifact(tenant_id=job.tenant_id, run_id=run.id, name="Page after the script", content_type=shots[n][0], data=shots[n][1]))
            row["run_id"], row["run_key"] = str(run.id), f"EX-{run.seq:03d}"
        if seconds is not None:
            offset += seconds
        snapshot.append(row)
    record.items = snapshot
    counts = {s: sum(1 for r in snapshot if r["status"] == s) for s in BATCH_RESULT_STATUSES}
    jobs.succeed(
        db,
        job.id,
        {"batch_run_id": str(record.id), "batch_run_key": batch_run_key(record.seq), "status": status, "counts": counts, "total": len(snapshot)},
        duration_ms=int((record.duration_seconds or 0) * 1000),
    )
