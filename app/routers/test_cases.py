"""Test cases: generate them from a scanned requirement (background job), list, read, delete, Word export. Tenant-scoped."""
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app import ai_providers, deletion, editing, jobs
from app.ai_settings import effective_ai, require_model
from app.db import get_db
from app.deps import current_user, scoped_select
from app.engines import docx_builder
from app.engines.tcg import TESTCASE_TYPES as CASE_TYPE_NAMES
from app.errors import AppError
from app.models import Tenant, User
from app.models_runner import Scan
from app.models_tests import TcCase, TcSet, TsScript
from app.models_work import Job, Requirement
from app.routers.projects import get_project
from app.routers.requirements import _job_dict

router = APIRouter(prefix="/api")

JOB_TYPE = "generate_test_cases"
SORTS = {"key": TcCase.seq, "title": TcCase.title, "type": TcCase.case_type, "created": TcCase.created_at}


def case_key(seq: int) -> str:
    return f"TC-{seq:03d}"


def _case_row(c: TcCase, req_key: str | None, script: dict | None) -> dict:
    return {
        "id": str(c.id),
        "key": case_key(c.seq),
        "seq": c.seq,
        "project_id": str(c.project_id),
        "requirement_id": str(c.requirement_id),
        "requirement_key": req_key,
        "set_id": str(c.set_id),
        "type": c.case_type,
        "title": c.title,
        "step_count": len(c.steps or []),
        "script": script,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
        "edited": c.edited,
        "version": c.version,
    }


def _req_keys(db: Session, user: User, ids: set) -> dict:
    if not ids:
        return {}
    rows = db.execute(scoped_select(Requirement, user).where(Requirement.id.in_(ids)).with_only_columns(Requirement.id, Requirement.seq)).all()
    return {rid: f"REQ-{seq:03d}" for rid, seq in rows}


def _script_info(db: Session, user: User, case_ids: list) -> dict:
    """case id -> the script of that case (filled in once scripts exist)."""
    from app.routers.test_scripts import scripts_for_cases

    return scripts_for_cases(db, user, case_ids)


def latest_usable_scan(db: Session, user: User, requirement_id: uuid.UUID) -> Scan | None:
    """The most recent scan of THIS requirement that produced an App Map (as in Pilot 1: never a scan of another one)."""
    return db.scalars(
        scoped_select(Scan, user)
        .where(Scan.requirement_id == requirement_id, func.jsonb_typeof(Scan.app_map) == "object", func.jsonb_typeof(Scan.tcg_contract) == "object", Scan.status != "FAILED")
        .order_by(Scan.created_at.desc())
        .limit(1)
    ).first()


def _get_requirement(db: Session, user: User, project_id: uuid.UUID, requirement_id: uuid.UUID) -> Requirement:
    req = db.scalar(scoped_select(Requirement, user).where(Requirement.id == requirement_id, Requirement.project_id == project_id))
    if req is None:
        raise AppError(404, "requirement_not_found", "Requirement not found.")
    return req


@router.get("/projects/{project_id}/requirements/{requirement_id}/test-cases/plan")
def generation_plan(project_id: uuid.UUID, requirement_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """What Generate Test Cases would use: the scan, the model, and whether it can start."""
    project = get_project(db, user, project_id)
    req = _get_requirement(db, user, project.id, requirement_id)
    scan = latest_usable_scan(db, user, req.id)
    ai = effective_ai(user, db.get(Tenant, user.tenant_id))
    running = db.scalar(
        scoped_select(Job, user).where(
            Job.project_id == project.id, Job.type == JOB_TYPE, Job.status.in_(("queued", "running")), Job.payload["requirement_id"].astext == str(req.id)
        )
    )
    return {
        "requirement_key": f"REQ-{req.seq:03d}",
        "scan": None
        if scan is None
        else {"id": str(scan.id), "status": scan.status, "created_at": scan.created_at.isoformat(), "application_url": scan.application_url, "model": scan.model},
        "provider": ai.provider,
        "model": ai.model,
        "model_chosen": ai.chosen,
        "running_job": _job_dict(running) if running else None,
    }


@router.post("/projects/{project_id}/requirements/{requirement_id}/test-cases/generate", status_code=202)
def generate_test_cases(project_id: uuid.UUID, requirement_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = get_project(db, user, project_id)
    req = _get_requirement(db, user, project.id, requirement_id)
    scan = latest_usable_scan(db, user, req.id)
    if scan is None:
        raise AppError(409, "no_scan", f"REQ-{req.seq:03d} has no saved App Scan yet. Run Scan App first, then generate the test cases.")
    ai = effective_ai(user, db.get(Tenant, user.tenant_id))
    require_model(ai)
    if not ai_providers.is_configured(ai.provider):
        raise AppError(400, "provider_not_configured", f"No API key for {ai_providers.PROVIDERS.get(ai.provider, ai.provider)} is configured on the server.")
    running = db.scalar(
        scoped_select(Job, user).where(
            Job.project_id == project.id, Job.type == JOB_TYPE, Job.status.in_(("queued", "running")), Job.payload["requirement_id"].astext == str(req.id)
        )
    )
    if running is not None:
        raise AppError(409, "already_running", "Test cases for this requirement are already being generated.")
    job = jobs.enqueue(
        db,
        tenant_id=user.tenant_id,
        job_type=JOB_TYPE,
        project_id=project.id,
        created_by=user.id,
        payload={
            "requirement_id": str(req.id),
            "requirement_key": f"REQ-{req.seq:03d}",
            "scan_id": str(scan.id),
            "provider": ai.provider,
            "model": ai.model,
            "ai_source": ai.source,
            "filename": f"REQ-{req.seq:03d}",
        },
    )
    return _job_dict(job)


@router.get("/projects/{project_id}/test-cases")
def list_test_cases(
    project_id: uuid.UUID,
    q: str = "",
    requirement_id: list[uuid.UUID] = Query(default=[]),
    type: list[str] = Query(default=[]),
    script: list[str] = Query(default=[]),
    created_from: date | None = None,
    created_to: date | None = None,
    sort: str = "key",
    direction: str = "asc",
    page: int = 1,
    page_size: int = 10,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Optional filters (combine with AND): `requirement_id` and `type` (repeatable), `script` = with_script / without_script / out_of_date
    (repeatable; a case matches any chosen state), `created_from` / `created_to` (inclusive days, UTC)."""
    get_project(db, user, project_id)
    types = [t.upper() for t in type]
    if any(t not in CASE_TYPE_NAMES for t in types):
        raise AppError(422, "invalid_filter", "type must be one of " + ", ".join(sorted(CASE_TYPE_NAMES)) + ".")
    if any(s not in ("with_script", "without_script", "out_of_date") for s in script):
        raise AppError(422, "invalid_filter", "script must be with_script, without_script or out_of_date.")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    stmt = scoped_select(TcCase, user).where(TcCase.project_id == project_id)
    if requirement_id:
        stmt = stmt.where(TcCase.requirement_id.in_(requirement_id))
    if types:
        stmt = stmt.where(TcCase.case_type.in_(types))
    if script:
        has = exists().where(TsScript.test_case_id == TcCase.id)
        stale = exists().where(TsScript.test_case_id == TcCase.id, TsScript.case_version != TcCase.version)
        wanted = []
        if "with_script" in script:
            wanted.append(has)
        if "without_script" in script:
            wanted.append(~has)
        if "out_of_date" in script:
            wanted.append(stale)
        stmt = stmt.where(or_(*wanted))
    if created_from:
        stmt = stmt.where(TcCase.created_at >= datetime.combine(created_from, time.min, tzinfo=timezone.utc))
    if created_to:
        stmt = stmt.where(TcCase.created_at < datetime.combine(created_to + timedelta(days=1), time.min, tzinfo=timezone.utc))
    q = q.strip()
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(or_(TcCase.title.ilike(f"%{esc}%", escape="\\"), TcCase.overview.ilike(f"%{esc}%", escape="\\"), TcCase.case_type.ilike(f"%{esc}%", escape="\\")))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    column = SORTS.get(sort, TcCase.seq)
    order = column.desc() if direction == "desc" else column.asc()
    rows = db.scalars(stmt.order_by(order, TcCase.seq).offset((page - 1) * page_size).limit(page_size)).all()
    keys = _req_keys(db, user, {c.requirement_id for c in rows})
    scripts = _script_info(db, user, [c.id for c in rows])
    return {
        "test_cases": [_case_row(c, keys.get(c.requirement_id), scripts.get(c.id)) for c in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _set_dict(s: TcSet) -> dict:
    return {
        "id": str(s.id),
        "requirement_id": str(s.requirement_id),
        "scan_id": str(s.scan_id) if s.scan_id else None,
        "provider": s.provider,
        "model": s.model,
        "prompt_sha256": s.prompt_sha256,
        "tokens_in": s.tokens_in,
        "tokens_out": s.tokens_out,
        "duration_seconds": s.duration_seconds,
        "attempts": s.attempts,
        "coverage": s.coverage,
        "warnings": s.warnings,
        "mapping_gaps": s.mapping_gaps,
        "not_generated": s.not_generated,
        "created_at": s.created_at.isoformat(),
    }


@router.get("/projects/{project_id}/test-cases/{case_id}")
def read_test_case(project_id: uuid.UUID, case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    c = db.scalar(scoped_select(TcCase, user).where(TcCase.id == case_id, TcCase.project_id == project_id))
    if c is None:
        raise AppError(404, "test_case_not_found", "Test case not found.")
    keys = _req_keys(db, user, {c.requirement_id})
    case_set = db.scalar(scoped_select(TcSet, user).where(TcSet.id == c.set_id))
    scripts = _script_info(db, user, [c.id])
    return {
        **_case_row(c, keys.get(c.requirement_id), scripts.get(c.id)),
        "overview": c.overview,
        "prerequisites": c.prerequisites,
        "objective": c.objective,
        "expected_result": c.expected_result,
        "steps": c.steps,
        "set": _set_dict(case_set) if case_set else None,
    }


@router.delete("/projects/{project_id}/test-cases/{case_id}", status_code=204)
def delete_test_case(project_id: uuid.UUID, case_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    result = deletion.delete_test_cases(db, user, project_id, [case_id])
    if result["blocked"]:
        raise AppError(409, "busy", result["blocked"][0]["reason"])
    if not result["deleted"]:
        raise AppError(404, "test_case_not_found", "Test case not found.")
    return Response(status_code=204)


@router.patch("/projects/{project_id}/test-cases/{case_id}")
def edit_test_case(project_id: uuid.UUID, case_id: uuid.UUID, body: editing.TestCaseEdit, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Edit the description fields and/or replace the steps. The script (if any) is then reported as out of date."""
    get_project(db, user, project_id)
    case, warnings = editing.edit_test_case(db, user, project_id, case_id, body)
    return {**read_test_case(project_id, case.id, user, db), "warnings": warnings}


@router.post("/projects/{project_id}/test-cases/delete")
def delete_test_cases(project_id: uuid.UUID, body: deletion.DeleteMany, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete several test cases (and their scripts). Items with a running job are returned as `blocked` and kept."""
    get_project(db, user, project_id)
    return deletion.delete_test_cases(db, user, project_id, body.ids)


@router.get("/projects/{project_id}/test-case-sets")
def list_sets(project_id: uuid.UUID, requirement_id: uuid.UUID | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    stmt = scoped_select(TcSet, user).where(TcSet.project_id == project_id)
    if requirement_id:
        stmt = stmt.where(TcSet.requirement_id == requirement_id)
    rows = db.scalars(stmt.order_by(TcSet.created_at.desc()).limit(50)).all()
    counts = dict(db.execute(select(TcCase.set_id, func.count()).where(TcCase.set_id.in_([r.id for r in rows])).group_by(TcCase.set_id)).all()) if rows else {}
    return {"sets": [{**_set_dict(s), "test_case_count": counts.get(s.id, 0)} for s in rows]}


@router.get("/projects/{project_id}/test-case-sets/{set_id}/docx")
def download_docx(project_id: uuid.UUID, set_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    s = db.scalar(scoped_select(TcSet, user).where(TcSet.id == set_id, TcSet.project_id == project_id))
    if s is None:
        raise AppError(404, "test_case_set_not_found", "Test case set not found.")
    cases = db.scalars(scoped_select(TcCase, user).where(TcCase.set_id == s.id).order_by(TcCase.seq)).all()
    if not cases:
        raise AppError(404, "no_test_cases", "All test cases of this set were deleted.")
    req_key = _req_keys(db, user, {s.requirement_id}).get(s.requirement_id, "REQ")
    data = docx_builder.build_test_cases_docx(
        requirement_id=req_key,
        scan_run_id=str(s.scan_id or ""),
        generation_run_id=str(s.id),
        model=s.model or "",
        generated_at=s.created_at.isoformat(),
        test_cases=[
            {
                "id": case_key(c.seq),
                "testCaseType": c.case_type,
                "title": c.title,
                "overview": c.overview,
                "preRequisites": c.prerequisites,
                "testObjective": c.objective,
                "expectedResult": c.expected_result,
                "steps": c.steps,
            }
            for c in cases
        ],
        not_generated=s.not_generated,
    )
    name = f"TestMax_Test_Cases_{req_key}_{s.created_at:%Y%m%d_%H%M%S}.docx"
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
