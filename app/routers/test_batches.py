"""Test batches: scripts that run in a chosen order in ONE browser session. Create, edit, delete, run, and read the run results. Tenant-scoped."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import jobs, runner_target
from app.batch_service import BATCH_RESULT_STATUSES, batch_key, batch_run_key
from app.db import get_db
from app.deps import current_user, scoped_select
from app.engines import batch as batch_engine
from app.engines import datadriven, tsg
from app.errors import AppError
from app.models import Project, User
from app.models_runner import Runner
from app.models_tests import TbBatch, TbItem, TbRun, TcCase, TsRun, TsScript
from app.models_work import Job
from app.routers.projects import get_project
from app.routers.requirements import _job_dict
from app.routers.test_runs import prepare_runner

router = APIRouter(prefix="/api")

RECENT_RUNS = 10
SORTS = {"key": TbBatch.seq, "name": TbBatch.name, "created": TbBatch.created_at, "updated": TbBatch.updated_at}


class ItemIn(BaseModel):
    script_id: uuid.UUID
    data_set_id: str = "default"  # "default" = DV-1, or the id of one of the test case's data variations


class BatchIn(BaseModel):
    name: str
    description: str = ""
    on_failure: Literal["stop", "continue"] = "stop"
    items: list[ItemIn] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Enter a name for the batch.")
        if len(v) > 120:
            raise ValueError("The name is too long (120 characters at most).")
        return v

    @field_validator("description")
    @classmethod
    def _description(cls, v: str) -> str:
        v = v.strip()
        if len(v) > 1000:
            raise ValueError("The description is too long (1000 characters at most).")
        return v

    @field_validator("items")
    @classmethod
    def _items(cls, v: list) -> list:
        if not v:
            raise ValueError("Add at least one test script.")
        if len(v) > batch_engine.MAX_ITEMS:
            raise ValueError(f"A batch can hold {batch_engine.MAX_ITEMS} scripts at most.")
        return v


class RunBatch(BaseModel):
    runner_id: uuid.UUID | None = None  # None = any online runner of the organization


# ---------------------------------------------------------------- reading
def _get_batch(db: Session, user: User, project_id: uuid.UUID, batch_id: uuid.UUID) -> TbBatch:
    b = db.scalar(scoped_select(TbBatch, user).where(TbBatch.id == batch_id, TbBatch.project_id == project_id))
    if b is None:
        raise AppError(404, "test_batch_not_found", "Test batch not found.")
    return b


def _set_choices(case: TcCase | None) -> list:
    return [{"id": "default", "label": datadriven.set_label(1, None)}] + [
        {"id": d["id"], "label": datadriven.set_label(i + 2, d)} for i, d in enumerate((case.data_sets if case else []) or [])
    ]


def _item_views(db: Session, user: User, batch_ids: list) -> dict:
    """batch id -> its items in order, each with the script, its test case, the chosen data variation and the variations to choose from."""
    if not batch_ids:
        return {}
    rows = db.scalars(scoped_select(TbItem, user).where(TbItem.batch_id.in_(batch_ids)).order_by(TbItem.batch_id, TbItem.position)).all()
    scripts = {s.id: s for s in db.scalars(scoped_select(TsScript, user).where(TsScript.id.in_({r.script_id for r in rows}))).all()} if rows else {}
    cases = {c.id: c for c in db.scalars(scoped_select(TcCase, user).where(TcCase.id.in_({s.test_case_id for s in scripts.values()}))).all()} if scripts else {}
    out: dict = {b: [] for b in batch_ids}
    for row in rows:
        s = scripts.get(row.script_id)
        if s is None:
            continue
        case = cases.get(s.test_case_id)
        choices = _set_choices(case)
        chosen = next((c for c in choices if c["id"] == row.data_set_id), None)
        out[row.batch_id].append(
            {
                "id": str(row.id),
                "position": len(out[row.batch_id]) + 1,
                "script_id": str(s.id),
                "script_key": f"TS-{s.seq:03d}",
                "test_case_id": str(s.test_case_id),
                "test_case_key": f"TC-{case.seq:03d}" if case else None,
                "title": case.title if case else "",
                "data_driven": bool(s.data_keys),
                "blocked": bool(s.blocked_message),
                "stale": bool(case is not None and s.case_version != case.version),
                "data_set_id": row.data_set_id,
                "data_set_label": chosen["label"] if chosen else None,
                "data_set_missing": chosen is None,
                "data_sets": choices if s.data_keys else choices[:1],
            }
        )
    return out


def _run_row(r: TbRun) -> dict:
    counts = {s: sum(1 for i in r.items if i.get("status") == s) for s in BATCH_RESULT_STATUSES}
    return {
        "id": str(r.id),
        "key": batch_run_key(r.seq),
        "batch_id": str(r.batch_id) if r.batch_id else None,
        "batch_key": r.batch_key,
        "batch_name": r.batch_name,
        "on_failure": r.on_failure,
        "status": r.status,
        "duration_seconds": r.duration_seconds,
        "headed": r.headed,
        "counts": counts,
        "total": len(r.items),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "created_at": r.created_at.isoformat(),
    }


def _batch_row(b: TbBatch, items: list, extra: dict | None = None) -> dict:
    return {
        "id": str(b.id),
        "key": batch_key(b.seq),
        "seq": b.seq,
        "project_id": str(b.project_id),
        "name": b.name,
        "description": b.description,
        "on_failure": b.on_failure,
        "item_count": len(items),
        "created_at": b.created_at.isoformat(),
        "updated_at": b.updated_at.isoformat(),
        **(extra or {}),
    }


def _running_job(db: Session, user: User, project_id: uuid.UUID, batch_id: uuid.UUID) -> Job | None:
    return db.scalar(
        scoped_select(Job, user).where(
            Job.project_id == project_id, Job.type == "run_script", Job.status.in_(("queued", "running")), Job.payload["batch_id"].astext == str(batch_id)
        )
    )


@router.get("/projects/{project_id}/test-batches")
def list_batches(
    project_id: uuid.UUID,
    q: str = "",
    sort: str = "key",
    direction: str = "asc",
    page: int = 1,
    page_size: int = 10,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    get_project(db, user, project_id)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    stmt = scoped_select(TbBatch, user).where(TbBatch.project_id == project_id)
    q = q.strip()
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(or_(TbBatch.name.ilike(f"%{esc}%", escape="\\"), TbBatch.description.ilike(f"%{esc}%", escape="\\")))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    column = SORTS.get(sort, TbBatch.seq)
    order = column.desc() if direction == "desc" else column.asc()
    rows = db.scalars(stmt.order_by(order, TbBatch.seq).offset((page - 1) * page_size).limit(page_size)).all()
    ids = [b.id for b in rows]
    items = _item_views(db, user, ids)
    last: dict = {}
    if ids:
        for r in db.scalars(scoped_select(TbRun, user).where(TbRun.batch_id.in_(ids)).order_by(TbRun.created_at.desc())).all():
            last.setdefault(r.batch_id, _run_row(r))
    return {"batches": [_batch_row(b, items[b.id], {"last_run": last.get(b.id)}) for b in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/projects/{project_id}/test-batches/{batch_id}")
def read_batch(project_id: uuid.UUID, batch_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    b = _get_batch(db, user, project_id, batch_id)
    items = _item_views(db, user, [b.id])[b.id]
    runs = db.scalars(scoped_select(TbRun, user).where(TbRun.batch_id == b.id).order_by(TbRun.created_at.desc()).limit(RECENT_RUNS)).all()
    running = _running_job(db, user, project_id, b.id)
    return {**_batch_row(b, items, {"items": items, "max_items": batch_engine.MAX_ITEMS, "recent_runs": [_run_row(r) for r in runs], "running_job": _job_dict(running) if running else None})}


# ---------------------------------------------------------------- writing
def _check_items(db: Session, user: User, project_id: uuid.UUID, items: list[ItemIn]) -> list[ItemIn]:
    ids = {i.script_id for i in items}
    scripts = {s.id: s for s in db.scalars(scoped_select(TsScript, user).where(TsScript.project_id == project_id, TsScript.id.in_(ids))).all()}
    if len(scripts) != len(ids):
        raise AppError(404, "test_script_not_found", "One of the test scripts was not found. Reload the page and choose again.")
    cases = {c.id: c for c in db.scalars(scoped_select(TcCase, user).where(TcCase.id.in_({s.test_case_id for s in scripts.values()}))).all()}
    for item in items:
        s = scripts[item.script_id]
        key = f"TS-{s.seq:03d}"
        try:
            batch_engine.split_script(s.code)
        except ValueError as exc:
            raise AppError(422, "script_not_batchable", f"{key} cannot be put in a batch: {exc}. Generate the script again.") from None
        if item.data_set_id != "default":
            if not s.data_keys:
                raise AppError(422, "not_data_driven", f"{key} does not read its values from a data block, so it can only use its own values (DV-1). Use \"Make data-driven\" on the script first.")
            if not any(d.get("id") == item.data_set_id for d in (cases[s.test_case_id].data_sets or [])):
                raise AppError(422, "data_set_not_found", f"The data variation chosen for {key} no longer exists. Choose another one.")
    return items


def _replace_items(db: Session, user: User, batch: TbBatch, items: list[ItemIn]) -> None:
    for old in db.scalars(scoped_select(TbItem, user).where(TbItem.batch_id == batch.id)).all():
        db.delete(old)
    db.flush()
    for position, item in enumerate(items, start=1):
        db.add(TbItem(tenant_id=user.tenant_id, batch_id=batch.id, script_id=item.script_id, position=position, data_set_id=item.data_set_id))


@router.post("/projects/{project_id}/test-batches", status_code=201)
def create_batch(project_id: uuid.UUID, body: BatchIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = get_project(db, user, project_id)
    _check_items(db, user, project.id, body.items)
    db.execute(select(Project.id).where(Project.id == project.id).with_for_update())  # one number sequence per project
    seq = db.scalar(select(func.coalesce(func.max(TbBatch.seq), 0)).where(TbBatch.project_id == project.id)) + 1
    batch = TbBatch(tenant_id=user.tenant_id, project_id=project.id, seq=seq, name=body.name, description=body.description, on_failure=body.on_failure, created_by=user.id)
    db.add(batch)
    db.flush()
    _replace_items(db, user, batch, body.items)
    db.commit()
    items = _item_views(db, user, [batch.id])[batch.id]
    return _batch_row(batch, items, {"items": items})


@router.put("/projects/{project_id}/test-batches/{batch_id}")
def update_batch(project_id: uuid.UUID, batch_id: uuid.UUID, body: BatchIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Replaces the name, description, setting and the whole list of scripts (last write wins)."""
    get_project(db, user, project_id)
    batch = db.scalar(scoped_select(TbBatch, user).where(TbBatch.id == batch_id, TbBatch.project_id == project_id).with_for_update())
    if batch is None:
        raise AppError(404, "test_batch_not_found", "Test batch not found.")
    if _running_job(db, user, project_id, batch.id) is not None:
        raise AppError(409, "already_running", "This batch is running. Wait until it ends (or cancel it), then edit it.")
    _check_items(db, user, project_id, body.items)
    batch.name, batch.description, batch.on_failure = body.name, body.description, body.on_failure
    batch.updated_at = func.now()
    _replace_items(db, user, batch, body.items)
    db.commit()
    db.refresh(batch)
    items = _item_views(db, user, [batch.id])[batch.id]
    return _batch_row(batch, items, {"items": items})


@router.delete("/projects/{project_id}/test-batches/{batch_id}", status_code=204)
def delete_batch(project_id: uuid.UUID, batch_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Deletes the batch and its batch run results. The scripts and the executions they produced stay."""
    get_project(db, user, project_id)
    batch = _get_batch(db, user, project_id, batch_id)
    if _running_job(db, user, project_id, batch.id) is not None:
        raise AppError(409, "already_running", "This batch is running. Wait until it ends (or cancel it), then delete it.")
    db.delete(batch)
    db.commit()


# ---------------------------------------------------------------- running
@router.post("/projects/{project_id}/test-batches/{batch_id}/run", status_code=202)
def run_batch(project_id: uuid.UUID, batch_id: uuid.UUID, body: RunBatch | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = get_project(db, user, project_id)
    batch = _get_batch(db, user, project.id, batch_id)
    key = batch_key(batch.seq)
    rows = db.scalars(scoped_select(TbItem, user).where(TbItem.batch_id == batch.id).order_by(TbItem.position)).all()
    if not rows:
        raise AppError(409, "empty_batch", "This batch has no test scripts left. Edit it and add some.")
    if _running_job(db, user, project.id, batch.id) is not None:
        raise AppError(409, "already_running", "This batch is already running.")
    scripts = {s.id: s for s in db.scalars(scoped_select(TsScript, user).where(TsScript.id.in_({r.script_id for r in rows}))).all()}
    cases = {c.id: c for c in db.scalars(scoped_select(TcCase, user).where(TcCase.id.in_({s.test_case_id for s in scripts.values()}))).all()}
    blocked = [f"TS-{scripts[r.script_id].seq:03d}" for r in rows if scripts[r.script_id].blocked_message]
    if blocked:
        raise AppError(409, "needs_test_data", f"{', '.join(dict.fromkeys(blocked))} cannot run yet: the test case has no real test data (see the script page). Fix it or take it out of the batch.")
    target = prepare_runner(db, user, body.runner_id if body else None)
    block_items, joined = [], []
    for position, row in enumerate(rows, start=1):
        s = scripts[row.script_id]
        case = cases.get(s.test_case_id)
        steps = (case.steps if case else []) or []
        try:
            _, number, data_set = datadriven.choose(case.data_sets if case else [], [row.data_set_id])[0]
        except AppError:
            raise AppError(409, "data_set_not_found", f"The data variation chosen for TS-{s.seq:03d} no longer exists. Edit the batch and choose another one.") from None
        if row.data_set_id != "default" and not s.data_keys:
            raise AppError(409, "not_data_driven", f"TS-{s.seq:03d} does not read its values from a data block, so it can only use its own values.")
        values = datadriven.values_for(steps, data_set)
        label = datadriven.set_label(number, data_set) if row.data_set_id != "default" else datadriven.set_label(1, None)
        code = datadriven.run_code(s.code, steps, data_set)
        script_key = f"TS-{s.seq:03d}"
        joined.append({"key": script_key, "code": code})
        block_items.append(
            {
                "position": position,
                "script_id": str(s.id),
                "script_key": script_key,
                "test_case_id": str(s.test_case_id),
                "test_case_key": f"TC-{case.seq:03d}" if case else None,
                "title": case.title if case else "",
                "code": code,
                "data_set": {"id": row.data_set_id, "name": label, "values": datadriven.mask_values(values)} if s.data_keys else None,
                "label": label if s.data_keys else None,
            }
        )
    try:
        script_text = batch_engine.build_script(batch.name, batch.on_failure, joined, key)
    except ValueError as exc:
        raise AppError(409, "script_not_batchable", f"The batch cannot be joined: {exc}. Generate that script again.") from None
    problem = tsg.check_syntax(script_text)
    if problem and problem.startswith("Syntax error"):
        raise AppError(409, "not_parseable", "The joined script did not pass the syntax check, so nothing was started. Generate the scripts again.")
    job = jobs.enqueue(
        db,
        tenant_id=user.tenant_id,
        job_type="run_script",
        project_id=project.id,
        created_by=user.id,
        max_attempts=1,  # a run is never repeated on its own: it may have changed data in the application under test
        target_runner_id=target.id if target else None,
        payload={
            **runner_target.target_payload(target),
            "batch_id": str(batch.id),
            "script_ids": [i["script_id"] for i in block_items],
            "script_key": key,
            "file_name": f"{key}.spec.js",
            "filename": f"{key} {batch.name}"[:80],
            "code": script_text,
            "batch": {"batch_id": str(batch.id), "key": key, "name": batch.name, "on_failure": batch.on_failure, "items": block_items},
        },
    )
    return {**_job_dict(job), "batch_id": str(batch.id), "items": [{"position": i["position"], "script_key": i["script_key"], "title": i["title"], "label": i["label"]} for i in block_items]}


# ---------------------------------------------------------------- the result of a run
def _get_run(db: Session, user: User, project_id: uuid.UUID, run_id: uuid.UUID) -> TbRun:
    r = db.scalar(scoped_select(TbRun, user).where(TbRun.id == run_id, TbRun.project_id == project_id))
    if r is None:
        raise AppError(404, "test_batch_run_not_found", "Batch run not found.")
    return r


@router.get("/projects/{project_id}/test-batch-runs/{run_id}")
def read_batch_run(project_id: uuid.UUID, run_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    r = _get_run(db, user, project_id, run_id)
    runner = db.get(Runner, r.runner_id) if r.runner_id else None
    by = db.get(User, r.created_by) if r.created_by else None
    # an execution that was deleted since (its script was deleted) is shown as text only
    alive = set(db.scalars(select(TsRun.id).where(TsRun.batch_run_id == r.id, TsRun.tenant_id == user.tenant_id)).all())
    items = [{**i, "run_id": i["run_id"] if i.get("run_id") and uuid.UUID(i["run_id"]) in alive else None} for i in r.items]
    return {
        **_run_row(r),
        "project_id": str(r.project_id),
        "items": items,
        "error": r.error,
        "log": r.log,
        "runner": runner.name if runner and runner.tenant_id == user.tenant_id else None,
        "executed_by": by.name if by and by.tenant_id == user.tenant_id else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }
