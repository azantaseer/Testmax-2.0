"""Test scripts: generate them from test cases (one background job per case), list, read, download. Tenant-scoped."""
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import ai_providers, deletion, jobs
from app.ai_settings import effective_ai, require_model
from app.db import get_db
from app.deps import current_user, scoped_select
from app.errors import AppError
from app.models import Tenant, User
from app.models_tests import TcCase, TcSet, TsScript
from app.models_work import Job
from app.routers.projects import get_project
from app.routers.requirements import _job_dict

router = APIRouter(prefix="/api")

JOB_TYPE = "generate_test_script"
SORTS = {"key": TsScript.seq, "created": TsScript.created_at, "updated": TsScript.updated_at}


def script_key(seq: int) -> str:
    return f"TS-{seq:03d}"


def scripts_for_cases(db: Session, user: User, case_ids: list) -> dict:
    """case id -> a small description of its script (used by the test case pages)."""
    if not case_ids:
        return {}
    rows = db.scalars(scoped_select(TsScript, user).where(TsScript.test_case_id.in_(case_ids))).all()
    versions = dict(db.execute(select(TcCase.id, TcCase.version).where(TcCase.id.in_(case_ids))).all())
    return {
        s.test_case_id: {"id": str(s.id), "key": script_key(s.seq), "blocked": bool(s.blocked_message), "stale": s.case_version != versions.get(s.test_case_id, s.case_version)}
        for s in rows
    }


def _file_name(s: TsScript, case: TcCase | None) -> str:
    return f"TC-{case.seq:03d}.spec.js" if case else f"{script_key(s.seq)}.spec.js"


def _row(s: TsScript, case: TcCase | None, extra: dict | None = None) -> dict:
    return {
        "id": str(s.id),
        "key": script_key(s.seq),
        "seq": s.seq,
        "project_id": str(s.project_id),
        "test_case_id": str(s.test_case_id),
        "test_case_key": f"TC-{case.seq:03d}" if case else None,
        "title": case.title if case else "",
        "type": case.case_type if case else None,
        "language": s.language,
        "blocked": bool(s.blocked_message),
        "stale": bool(case is not None and s.case_version != case.version),  # the test case was edited after this script was written
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
        **(extra or {}),
    }


class GenerateScripts(BaseModel):
    test_case_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


@router.post("/projects/{project_id}/test-scripts/generate", status_code=202)
def generate_scripts(project_id: uuid.UUID, body: GenerateScripts, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = get_project(db, user, project_id)
    ids = list(dict.fromkeys(body.test_case_ids))
    cases = {c.id: c for c in db.scalars(scoped_select(TcCase, user).where(TcCase.project_id == project.id, TcCase.id.in_(ids))).all()}
    if len(cases) != len(ids):
        raise AppError(404, "test_case_not_found", "One of the test cases was not found.")
    ai = effective_ai(user, db.get(Tenant, user.tenant_id))
    require_model(ai)
    if not ai_providers.is_configured(ai.provider):
        raise AppError(400, "provider_not_configured", f"No API key for {ai_providers.PROVIDERS.get(ai.provider, ai.provider)} is configured on the server.")
    sets = {s.id: s for s in db.scalars(scoped_select(TcSet, user).where(TcSet.id.in_({c.set_id for c in cases.values()}))).all()}
    active = {
        j.payload.get("test_case_id")
        for j in db.scalars(scoped_select(Job, user).where(Job.project_id == project.id, Job.type == JOB_TYPE, Job.status.in_(("queued", "running")))).all()
    }
    started, skipped = [], []
    for case_id in ids:
        case = cases[case_id]
        key = f"TC-{case.seq:03d}"
        case_set = sets.get(case.set_id)
        if str(case.id) in active:
            skipped.append({"test_case_id": str(case.id), "key": key, "reason": "A script for this test case is already being generated."})
            continue
        if case_set is None or case_set.scan_id is None:
            skipped.append({"test_case_id": str(case.id), "key": key, "reason": "The scan these test cases were built from is gone. Scan the app and generate the test cases again."})
            continue
        job = jobs.enqueue(
            db,
            tenant_id=user.tenant_id,
            job_type=JOB_TYPE,
            project_id=project.id,
            created_by=user.id,
            payload={
                "test_case_id": str(case.id),
                "test_case_key": key,
                "provider": ai.provider,
                "model": ai.model,
                "ai_source": ai.source,
                "filename": key,
            },
        )
        started.append(_job_dict(job))
    return {"jobs": started, "skipped": skipped}


@router.get("/projects/{project_id}/test-scripts")
def list_scripts(
    project_id: uuid.UUID,
    q: str = "",
    type: list[str] = Query(default=[]),
    state: list[str] = Query(default=[]),
    created_from: date | None = None,
    created_to: date | None = None,
    sort: str = "key",
    direction: str = "asc",
    page: int = 1,
    page_size: int = 10,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Optional filters (combine with AND): `type` = the test case type (repeatable), `state` = up_to_date / out_of_date / blocked
    (repeatable; a script matches any chosen state), `created_from` / `created_to` (inclusive days, UTC)."""
    get_project(db, user, project_id)
    types = [t.upper() for t in type]
    if any(t not in ("POSITIVE", "NEGATIVE", "VERIFICATION", "BOUNDARY", "EDGE") for t in types):
        raise AppError(422, "invalid_filter", "type must be POSITIVE, NEGATIVE, VERIFICATION, BOUNDARY or EDGE.")
    if any(s not in ("up_to_date", "out_of_date", "blocked") for s in state):
        raise AppError(422, "invalid_filter", "state must be up_to_date, out_of_date or blocked.")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    stmt = scoped_select(TsScript, user).where(TsScript.project_id == project_id).join(TcCase, TcCase.id == TsScript.test_case_id)
    q = q.strip()
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(or_(TcCase.title.ilike(f"%{esc}%", escape="\\"), TsScript.code.ilike(f"%{esc}%", escape="\\")))
    if types:
        stmt = stmt.where(TcCase.case_type.in_(types))
    if state:
        blocked = func.coalesce(TsScript.blocked_message, "") != ""
        stale = TsScript.case_version != TcCase.version
        wanted = []
        if "blocked" in state:
            wanted.append(blocked)
        if "out_of_date" in state:
            wanted.append(~blocked & stale)
        if "up_to_date" in state:
            wanted.append(~blocked & ~stale)
        stmt = stmt.where(or_(*wanted))
    if created_from:
        stmt = stmt.where(TsScript.created_at >= datetime.combine(created_from, time.min, tzinfo=timezone.utc))
    if created_to:
        stmt = stmt.where(TsScript.created_at < datetime.combine(created_to + timedelta(days=1), time.min, tzinfo=timezone.utc))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    column = SORTS.get(sort, TsScript.seq)
    order = column.desc() if direction == "desc" else column.asc()
    rows = db.scalars(stmt.order_by(order, TsScript.seq).offset((page - 1) * page_size).limit(page_size)).all()
    cases = {c.id: c for c in db.scalars(scoped_select(TcCase, user).where(TcCase.id.in_([r.test_case_id for r in rows]))).all()} if rows else {}
    from app.routers.test_runs import last_runs

    runs = last_runs(db, user, [r.id for r in rows])
    return {"scripts": [_row(s, cases.get(s.test_case_id), {"last_run": runs.get(s.id)}) for s in rows], "total": total, "page": page, "page_size": page_size}


def _get_script(db: Session, user: User, project_id: uuid.UUID, script_id: uuid.UUID) -> TsScript:
    s = db.scalar(scoped_select(TsScript, user).where(TsScript.id == script_id, TsScript.project_id == project_id))
    if s is None:
        raise AppError(404, "test_script_not_found", "Test script not found.")
    return s


@router.get("/projects/{project_id}/test-scripts/{script_id}")
def read_script(project_id: uuid.UUID, script_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    s = _get_script(db, user, project_id, script_id)
    case = db.scalar(scoped_select(TcCase, user).where(TcCase.id == s.test_case_id))
    from app.routers.test_runs import run_stats

    return {
        **_row(s, case),
        "code": s.code,
        "framework": "Playwright",
        "file_name": _file_name(s, case),
        "provider": s.provider,
        "model": s.model,
        "prompt_sha256": s.prompt_sha256,
        "tokens_in": s.tokens_in,
        "tokens_out": s.tokens_out,
        "duration_seconds": s.duration_seconds,
        "attempts": s.attempts,
        "elements_used": s.elements_used,
        "steps_without_locator": s.steps_without_locator,
        "not_automated": s.not_automated,
        "blocked_message": s.blocked_message,
        "run_stats": run_stats(db, user, s.id),
    }


@router.post("/projects/{project_id}/test-scripts/delete")
def delete_scripts(project_id: uuid.UUID, body: deletion.DeleteMany, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete several test scripts (and their executions); the test cases stay. Items with a running job are returned as `blocked`."""
    get_project(db, user, project_id)
    return deletion.delete_test_scripts(db, user, project_id, body.ids)


@router.get("/projects/{project_id}/test-scripts/{script_id}/download")
def download_script(project_id: uuid.UUID, script_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    s = _get_script(db, user, project_id, script_id)
    case = db.scalar(scoped_select(TcCase, user).where(TcCase.id == s.test_case_id))
    return Response(s.code, media_type="text/javascript", headers={"Content-Disposition": f'attachment; filename="{_file_name(s, case)}"'})
