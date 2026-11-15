"""Start a scan from a requirement, list scans, read a scan, cancel a job. Tenant-scoped."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai_providers, jobs, runner_target, scan_service
from app.ai_settings import effective_ai, require_model
from app.db import get_db
from app.deps import current_user, scoped_select
from app.engines import scan_core as sc
from app.errors import AppError
from app.models import Tenant, User
from app.models_runner import Runner, Scan
from app.models_work import Job, Requirement
from app.routers.projects import get_project
from app.runner_auth import is_online

router = APIRouter(prefix="/api")


class StartScan(BaseModel):
    application_url: str = Field(max_length=2000)
    scan_policy: str = "INTERACTIVE"
    test_environment_authorized: bool = False
    summary: str | None = Field(default=None, max_length=1000)
    description: str | None = Field(default=None, max_length=60000)
    acceptance_criteria: str | None = Field(default=None, max_length=20000)
    runner_id: uuid.UUID | None = None  # None = any online runner of the organization


def _scan_row(s: Scan, req_key: str | None = None) -> dict:
    pages, controls = scan_service.count_pages_controls(s.app_map)
    return {
        "id": str(s.id),
        "project_id": str(s.project_id),
        "requirement_id": str(s.requirement_id) if s.requirement_id else None,
        "requirement_key": req_key or (s.requirement_snapshot or {}).get("requirement_key"),
        "status": s.status,
        "stop_reason": s.stop_reason,
        "application_url": s.application_url,
        "scan_policy": s.scan_policy,
        "provider": s.provider,
        "model": s.model,
        "tool_call_count": s.tool_call_count,
        "duration_seconds": s.duration_seconds,
        "pages": pages,
        "controls": controls,
        "issues": len(s.schema_issues or []),
        "created_at": s.created_at.isoformat(),
    }


@router.post("/projects/{project_id}/requirements/{requirement_id}/scan", status_code=202)
def start_scan(project_id: uuid.UUID, requirement_id: uuid.UUID, body: StartScan, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = get_project(db, user, project_id)
    req = db.scalar(scoped_select(Requirement, user).where(Requirement.id == requirement_id, Requirement.project_id == project.id))
    if req is None:
        raise AppError(404, "requirement_not_found", "Requirement not found.")
    url = body.application_url.strip()
    if not sc.is_valid_http_url(url):
        raise AppError(422, "bad_url", "Application URL must be a valid http(s) URL.")
    if body.scan_policy not in ("INTERACTIVE", "READ_ONLY"):
        raise AppError(422, "bad_policy", "Scan policy must be INTERACTIVE or READ_ONLY.")
    if body.scan_policy == "INTERACTIVE" and not body.test_environment_authorized:
        raise AppError(
            422,
            "authorization_required",
            "Autonomous execution (INTERACTIVE) requires explicit confirmation that this URL is an authorized test environment. "
            "Confirm the test-environment checkbox, or use READ_ONLY to inspect without that authorization.",
        )
    target = runner_target.resolve_target(db, user, body.runner_id)
    runners = db.scalars(scoped_select(Runner, user).where(Runner.revoked_at.is_(None))).all()
    if not any(is_online(r) for r in runners):
        raise AppError(
            409,
            "no_runner_online",
            "No runner is online for your organization. Connect a runner (see the Runners page), then try again.",
        )
    ai = effective_ai(user, db.get(Tenant, user.tenant_id))
    require_model(ai)
    if not ai_providers.is_configured(ai.provider):
        raise AppError(400, "provider_not_configured", f"No API key for {ai_providers.PROVIDERS.get(ai.provider, ai.provider)} is configured on the server.")
    can_scan, reason = ai_providers.check_tool_calling(ai.provider, ai.model)
    if can_scan is False:
        raise AppError(
            400,
            "model_cannot_scan",
            f"The selected model ({ai.model}) cannot be used for app scans because scans need tool calling: {reason} "
            "Choose another model in My Settings (or Organization AI Model) and try again.",
        )
    job = jobs.enqueue(
        db,
        tenant_id=user.tenant_id,
        job_type="app_scan",
        project_id=project.id,
        created_by=user.id,
        max_attempts=2,
        target_runner_id=target.id if target else None,
        payload={
            **runner_target.target_payload(target),
            "requirement_id": str(req.id),
            "requirement_key": f"REQ-{req.seq:03d}",
            "summary": body.summary if body.summary is not None else req.summary,
            "description": body.description if body.description is not None else req.description,
            "acceptance_criteria": body.acceptance_criteria if body.acceptance_criteria is not None else req.acceptance_criteria,
            "application_url": url,
            "scan_policy": body.scan_policy,
            "test_environment_authorized": body.test_environment_authorized,
            "provider": ai.provider,
            "model": ai.model,
            "ai_source": ai.source,
            "prompt_sha256": sc.load_system_prompt()[1],
            "filename": f"REQ-{req.seq:03d}",
        },
    )
    return {"id": str(job.id), "status": job.status, "progress": job.progress}


@router.get("/projects/{project_id}/scans")
def list_scans(project_id: uuid.UUID, requirement_id: uuid.UUID | None = None, limit: int = 20, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    stmt = scoped_select(Scan, user).where(Scan.project_id == project_id)
    if requirement_id:
        stmt = stmt.where(Scan.requirement_id == requirement_id)
    rows = db.scalars(stmt.order_by(Scan.created_at.desc()).limit(min(max(limit, 1), 100))).all()
    return {"scans": [_scan_row(s) for s in rows]}


@router.get("/projects/{project_id}/scans/{scan_id}")
def read_scan(project_id: uuid.UUID, scan_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project(db, user, project_id)
    s = db.scalar(scoped_select(Scan, user).where(Scan.id == scan_id, Scan.project_id == project_id))
    if s is None:
        raise AppError(404, "scan_not_found", "Scan not found.")
    return {
        **_scan_row(s),
        "app_map": s.app_map,
        "raw": s.raw,
        "schema_issues": s.schema_issues,
        "history": s.history,
        "requirement_snapshot": s.requirement_snapshot,
        "contract_issues": s.contract_issues,
        "prompt_sha256": s.prompt_sha256,
        "notes": s.notes or [],
    }


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.scalar(scoped_select(Job, user).where(Job.id == job_id))
    if job is None:
        raise AppError(404, "job_not_found", "Job not found.")
    if user.role == "user" and job.created_by != user.id:
        raise AppError(403, "forbidden", "Only the person who started a job or an admin can cancel it.")
    return {"result": jobs.request_cancel(db, job_id)}
