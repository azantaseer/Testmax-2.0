"""Server side of the app scan: the prompt the proxy uses, and saving what the runner reports.

The runner does the browsing. The server re-checks everything it is told: it validates the App Map again, scrubs
known secrets again, derives the status itself, and builds the test-case and script input contracts.
"""
import uuid

from sqlalchemy.orm import Session

from app import jobs
from app.engines import scan_core as sc
from app.models_runner import Runner, Scan
from app.models_work import Job
from app.runner_jobs import COMPLETERS, SYSTEM_PROMPTS

VALID = ("COMPLETE", "PARTIAL", "FAILED")


def _prompt(job: Job) -> str:
    return sc.load_system_prompt()[0]


def count_pages_controls(app_map: dict | None) -> tuple[int, int]:
    pages = (app_map or {}).get("pages") or []
    controls = sum(len(p.get("controls") or []) for p in pages if isinstance(p, dict))
    return len(pages), controls


def complete_app_scan(db: Session, job: Job, runner: Runner, result: dict) -> None:
    payload = job.payload
    snapshot_texts = (payload.get("summary", ""), payload.get("description", ""), payload.get("acceptance_criteria", ""))
    secrets = sc.extract_known_secrets(*snapshot_texts)

    app_map = result.get("app_map")
    app_map = sc.scrub_secrets(app_map, secrets) if isinstance(app_map, dict) else None
    status = str(result.get("status", "FAILED")).upper()
    stop_reason = str(sc.scrub_secrets(result.get("stop_reason") or "", secrets))[:1000]
    issues: list = []
    if app_map is not None:
        issues = sc.validate_app_map_schema(app_map)
        status, note = sc.derive_validated_status(app_map, status if status in VALID else "PARTIAL")
        app_map["status"] = status
        if note:
            stop_reason = note
    elif status != "FAILED":
        status, stop_reason = "FAILED", stop_reason or "The scan returned no App Map."
    if status not in VALID:
        status = "FAILED"

    history = sc.scrub_secrets(result.get("history") if isinstance(result.get("history"), list) else [], secrets)
    raw = result.get("raw")
    raw = sc.scrub_secrets(raw, secrets) if isinstance(raw, str) else None
    prompt_sha = payload.get("prompt_sha256") or sc.load_system_prompt()[1]
    requirement_id = payload.get("requirement_id")
    snapshot = {
        "requirement_id": requirement_id,
        "requirement_key": payload.get("requirement_key"),
        "summary": payload.get("summary", ""),
        "description": payload.get("description", ""),
        "acceptance_criteria": payload.get("acceptance_criteria", ""),
        "captured_for_run_id": str(job.id),
    }

    scan = Scan(
        id=uuid.uuid4(),
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        requirement_id=uuid.UUID(requirement_id) if requirement_id else None,
        job_id=job.id,
        runner_id=runner.id,
        application_url=payload.get("application_url", "") if sc.is_valid_http_url(payload.get("application_url", "")) else "[invalid value withheld]",
        scan_policy=payload.get("scan_policy", "INTERACTIVE"),
        status=status,
        stop_reason=stop_reason,
        provider=payload.get("provider"),
        model=payload.get("model"),
        prompt_sha256=prompt_sha,
        tool_call_count=int(result.get("tool_call_count") or len(history)),
        duration_seconds=int(result.get("duration_seconds") or 0),
        requirement_snapshot=snapshot,
        app_map=app_map,
        raw=raw,
        schema_issues=issues,
        history=history,
        notes=list(payload.get("notes") or []),
    )
    if app_map is not None:
        record = {
            "run_id": str(scan.id),
            "requirement_id": requirement_id,
            "application_url": scan.application_url,
            "model": scan.model,
            "scan_policy": scan.scan_policy,
            "status": status,
        }
        snap = {"summary": snapshot["summary"], "description": snapshot["description"], "acceptance_criteria": snapshot["acceptance_criteria"]}
        tcg = sc.build_tcg_input_contract(record, snap, app_map)
        tsg = sc.build_tsg_input_contract(record, app_map)
        scan.tcg_contract, scan.tsg_contract = tcg, tsg
        scan.contract_issues = sc.validate_contracts(tcg, tsg, app_map)
    db.add(scan)

    pages, controls = count_pages_controls(app_map)
    summary = {"scan_id": str(scan.id), "status": status, "pages": pages, "controls": controls, "tool_calls": scan.tool_call_count}
    if status == "FAILED":
        db.commit()  # keep the scan record (its history explains the failure), then fail the job
        jobs.fail(db, job.id, stop_reason or "The scan failed.")
        return
    jobs.succeed(db, job.id, summary, provider=scan.provider, model=scan.model, duration_ms=scan.duration_seconds * 1000)


COMPLETERS["app_scan"] = complete_app_scan
SYSTEM_PROMPTS["app_scan"] = _prompt
