"""Requirements: upload a BRD, generate requirements as a background job, list and read them. Tenant-scoped."""
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app import ai_providers, deletion, editing, jobs
from app.ai_settings import effective_ai, require_model
from app.db import get_db
from app.deps import current_user, scoped_select
from app.engines import requirements as req_engine
from app.errors import AppError
from app.models import Tenant, User
from app.models_runner import Scan
from app.models_work import Document, Job, Requirement
from app.routers.projects import get_project

router = APIRouter(prefix="/api")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
JOB_TYPE = "generate_requirements"


def _job_dict(job: Job) -> dict:
    # A "running" job whose lease ran out has lost its worker; say so instead of showing stale progress.
    stalled = bool(job.status == "running" and job.lease_expires_at and job.lease_expires_at < datetime.now(timezone.utc))
    return {
        "stalled": stalled,
        "id": str(job.id),
        "type": job.type,
        "project_id": str(job.project_id) if job.project_id else None,
        "status": job.status,
        "progress": "The worker running this job stopped responding. It will be retried as soon as a worker is available."
        if stalled
        else job.progress,
        "error": job.error,
        "result": job.result,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "provider": (job.provider or job.payload.get("provider")),
        "model": (job.model or job.payload.get("model")),
        "filename": job.payload.get("filename"),
        "requirement_id": job.payload.get("requirement_id"),
        "test_case_id": job.payload.get("test_case_id"),
        "target_runner": job.payload.get("target_runner"),
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _latest_scans(db: Session, user: User, ids: list) -> dict:
    """requirement id -> {id, status} of its most recent scan."""
    if not ids:
        return {}
    rows = db.scalars(scoped_select(Scan, user).where(Scan.requirement_id.in_(ids)).order_by(Scan.created_at.desc())).all()
    out: dict = {}
    for s in rows:
        out.setdefault(s.requirement_id, {"id": str(s.id), "status": s.status})
    return out


def _req_dict(r: Requirement, full: bool, scan: dict | None = None) -> dict:
    out = {
        "scan": scan,
        "id": str(r.id),
        "project_id": str(r.project_id),
        "key": f"REQ-{r.seq:03d}",
        "seq": r.seq,
        "summary": r.summary,
        "provider": r.provider,
        "model": r.model,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
        "edited": r.edited,
        "version": r.version,
    }
    if full:
        out["description"] = r.description
        out["acceptance_criteria"] = r.acceptance_criteria
    return out


@router.post("/projects/{project_id}/requirements/generate", status_code=202)
def generate_requirements(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    project = get_project(db, user, project_id)
    ai = effective_ai(user, db.get(Tenant, user.tenant_id))
    require_model(ai)
    if not ai_providers.is_configured(ai.provider):
        raise AppError(
            400,
            "provider_not_configured",
            f"No API key for {ai_providers.PROVIDERS.get(ai.provider, ai.provider)} is configured on the server.",
        )
    filename = (file.filename or "").replace("\\", "/").split("/")[-1][:200]
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise AppError(413, "file_too_large", "The file is larger than 10 MB.")
    try:
        text = req_engine.extract_text(filename, content)
    except req_engine.DocumentError as err:
        raise AppError(422, "bad_document", str(err)) from None

    document = Document(
        tenant_id=user.tenant_id, project_id=project.id, filename=filename, size=len(content), data=content, text=text, created_by=user.id
    )
    db.add(document)
    db.flush()
    # The provider and model are fixed now (user setting > organization setting > system default),
    # so changing a setting later does not alter a job that is already queued.
    job = jobs.enqueue(
        db,
        tenant_id=user.tenant_id,
        job_type=JOB_TYPE,
        project_id=project.id,
        created_by=user.id,
        payload={"document_id": str(document.id), "filename": filename, "provider": ai.provider, "model": ai.model, "ai_source": ai.source},
    )
    return _job_dict(job)


@router.get("/projects/{project_id}/requirements")
def list_requirements(
    project_id: uuid.UUID,
    q: str = "",
    page: int = 1,
    page_size: int = 25,
    scan: list[str] = Query(default=[]),
    model: list[str] = Query(default=[]),
    created_from: date | None = None,
    created_to: date | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Optional filters (all combine with AND): `scan` = scanned / not_scanned (both or neither = no filter), `model` (one or more
    generating model names), `created_from` / `created_to` (inclusive days, UTC). `models` in the answer lists the models used in the project."""
    get_project(db, user, project_id)
    bad = [s for s in scan if s not in ("scanned", "not_scanned")]
    if bad:
        raise AppError(422, "invalid_filter", "scan must be scanned or not_scanned.")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    stmt = scoped_select(Requirement, user).where(Requirement.project_id == project_id)
    q = q.strip()
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(or_(Requirement.summary.ilike(f"%{esc}%", escape="\\"), Requirement.description.ilike(f"%{esc}%", escape="\\")))
    if set(scan) == {"scanned"}:
        stmt = stmt.where(exists().where(Scan.requirement_id == Requirement.id))
    elif set(scan) == {"not_scanned"}:
        stmt = stmt.where(~exists().where(Scan.requirement_id == Requirement.id))
    if model:
        stmt = stmt.where(Requirement.model.in_(model))
    if created_from:
        stmt = stmt.where(Requirement.created_at >= datetime.combine(created_from, time.min, tzinfo=timezone.utc))
    if created_to:
        stmt = stmt.where(Requirement.created_at < datetime.combine(created_to + timedelta(days=1), time.min, tzinfo=timezone.utc))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Requirement.seq).offset((page - 1) * page_size).limit(page_size)).all()
    scans = _latest_scans(db, user, [r.id for r in rows])
    models = db.scalars(
        select(Requirement.model)
        .where(Requirement.tenant_id == user.tenant_id, Requirement.project_id == project_id, Requirement.model.is_not(None))
        .distinct()
        .order_by(Requirement.model)
    ).all()
    return {
        "requirements": [_req_dict(r, False, scans.get(r.id)) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "models": list(models),
    }


@router.patch("/projects/{project_id}/requirements/{requirement_id}")
def edit_requirement(project_id: uuid.UUID, requirement_id: uuid.UUID, body: editing.RequirementEdit, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Edit the summary, description or acceptance criteria. Existing scans and test cases are not changed."""
    get_project(db, user, project_id)
    req = editing.edit_requirement(db, user, project_id, requirement_id, body)
    return _req_dict(req, True, _latest_scans(db, user, [req.id]).get(req.id))


@router.post("/projects/{project_id}/requirements/delete")
def delete_requirements(project_id: uuid.UUID, body: deletion.DeleteMany, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete several requirements with everything built on them (scans, test cases, scripts, executions).
    Items with a running job are returned as `blocked` and kept."""
    get_project(db, user, project_id)
    return deletion.delete_requirements(db, user, project_id, body.ids)


@router.get("/projects/{project_id}/requirements/{requirement_id}")
def read_requirement(project_id: uuid.UUID, requirement_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    r = db.scalar(scoped_select(Requirement, user).where(Requirement.id == requirement_id, Requirement.project_id == project_id))
    if r is None:
        raise AppError(404, "requirement_not_found", "Requirement not found.")
    return _req_dict(r, True, _latest_scans(db, user, [r.id]).get(r.id))


@router.get("/projects/{project_id}/jobs")
def list_project_jobs(project_id: uuid.UUID, type: str = JOB_TYPE, limit: int = 10, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    limit = min(max(limit, 1), 50)
    rows = db.scalars(
        scoped_select(Job, user).where(Job.project_id == project_id, Job.type == type).order_by(Job.created_at.desc()).limit(limit)
    ).all()
    return {"jobs": [_job_dict(j) for j in rows]}


@router.get("/jobs/{job_id}")
def read_job(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.scalar(scoped_select(Job, user).where(Job.id == job_id))
    if job is None:
        raise AppError(404, "job_not_found", "Job not found.")
    return _job_dict(job)
