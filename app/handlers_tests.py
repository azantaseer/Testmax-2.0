"""Job handlers for test case generation and test script generation (server workers)."""
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import jobs, llm, providers
from app.engines import datadriven, tcg, tsg
from app.models import Project
from app.models_runner import Scan
from app.models_tests import TcCase, TcSet, TsScript
from app.models_work import Job, Requirement


def _fail_types():
    from app.handlers import JobFailed, Retryable

    return JobFailed, Retryable


def generate_test_cases(db: Session, job: Job) -> None:
    JobFailed, Retryable = _fail_types()
    provider = job.payload.get("provider", "")
    model = job.payload.get("model", "")
    requirement = db.get(Requirement, job.payload.get("requirement_id"))
    if requirement is None or requirement.tenant_id != job.tenant_id or requirement.project_id != job.project_id:
        raise JobFailed("The requirement no longer exists.")
    scan = db.get(Scan, job.payload.get("scan_id"))
    if scan is None or scan.tenant_id != job.tenant_id or scan.project_id != job.project_id:
        raise JobFailed("The application scan these test cases were to be built from no longer exists.")
    # Pilot 1 refuses to combine a requirement with the scan of another one.
    if scan.requirement_id != requirement.id:
        raise JobFailed("The scan belongs to a different requirement. Refusing to combine a requirement with an unrelated scan.")
    if not scan.app_map or not scan.tcg_contract:
        raise JobFailed(f"The scan has no usable App Map (status: {scan.status}). Scan the app again.")

    jobs.set_progress(db, job.id, "Reading the requirement and the App Map")
    system_prompt, prompt_sha = tcg.load_system_prompt()
    adapter = providers.get(provider)
    label = adapter.label if adapter else provider

    def converse(messages, max_output_tokens):
        return llm.converse(provider, model, system_prompt, messages, max_output_tokens)

    def progress(text: str) -> None:
        jobs.set_progress(db, job.id, f"{text} ({label} {model})")

    usage: dict = {}
    started = time.monotonic()
    try:
        out = tcg.run_generation(
            converse,
            requirement_key=f"REQ-{requirement.seq:03d}",
            scan_id=str(scan.id),
            tcg_contract=scan.tcg_contract,
            app_map=scan.app_map,
            system_prompt=system_prompt,
            on_progress=progress,
            usage=usage,
        )
    except tcg.GenerationError as err:
        jobs.add_usage(db, job.id, usage.get("tokens_in"), usage.get("tokens_out"))
        raise (Retryable(err.message) if err.retryable else JobFailed(err.message)) from None
    duration = int(time.monotonic() - started)

    jobs.set_progress(db, job.id, "Saving the test cases")
    normalized = out["normalized"]
    # One test case number sequence per project; the project row lock keeps concurrent jobs from clashing.
    db.execute(select(Project.id).where(Project.id == job.project_id).with_for_update())
    base = db.scalar(select(func.coalesce(func.max(TcCase.seq), 0)).where(TcCase.project_id == job.project_id))
    case_set = TcSet(
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        requirement_id=requirement.id,
        scan_id=scan.id,
        job_id=job.id,
        provider=provider,
        model=model,
        prompt_sha256=prompt_sha,
        tokens_in=out["tokens_in"],
        tokens_out=out["tokens_out"],
        duration_seconds=duration,
        attempts=out["attempts"],
        coverage=out["coverage"],
        warnings=out["warnings"],
        mapping_gaps=out["mapping_gaps"],
        not_generated=normalized["notGenerated"],
        raw=out["raw"],
        created_by=job.created_by,
    )
    db.add(case_set)
    db.flush()
    db.add_all(
        TcCase(
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            requirement_id=requirement.id,
            set_id=case_set.id,
            seq=base + index,
            case_type=tc["testCaseType"],
            title=tc["title"],
            overview=tc["overview"],
            prerequisites=tc["preRequisites"],
            objective=tc["testObjective"],
            expected_result=tc["expectedResult"],
            steps=tc["steps"],
            set_position=tc["id"],
            created_by=job.created_by,
        )
        for index, tc in enumerate(normalized["testCases"], start=1)
    )
    jobs.succeed(
        db,
        job.id,
        {
            "set_id": str(case_set.id),
            "requirement_id": str(requirement.id),
            "test_case_count": len(normalized["testCases"]),
            "first_seq": base + 1,
            "coverage": out["coverage"],
            "prompt_sha256": prompt_sha,
        },
        provider=provider,
        model=model,
        tokens_in=out["tokens_in"],
        tokens_out=out["tokens_out"],
        duration_ms=duration * 1000,
    )


def generate_test_script(db: Session, job: Job) -> None:
    JobFailed, Retryable = _fail_types()
    provider = job.payload.get("provider", "")
    model = job.payload.get("model", "")
    case = db.get(TcCase, job.payload.get("test_case_id"))
    if case is None or case.tenant_id != job.tenant_id or case.project_id != job.project_id:
        raise JobFailed("The test case no longer exists.")
    case_version = case.version  # the version this script is written from; a later edit makes the script "out of date"
    case_set = db.get(TcSet, case.set_id)
    scan = db.get(Scan, case_set.scan_id) if case_set and case_set.scan_id else None
    if scan is None or scan.tenant_id != job.tenant_id or not isinstance(scan.app_map, dict):
        raise JobFailed("The application scan these test cases were built from is no longer available. Scan the app and generate the test cases again.")

    jobs.set_progress(db, job.id, "Building locators from the scan")
    system_prompt, prompt_sha = tsg.load_system_prompt()
    adapter = providers.get(provider)
    label = adapter.label if adapter else provider
    engine_case = {
        "id": f"TC-{case.seq:03d}",
        "title": case.title,
        "testCaseType": case.case_type,
        "overview": case.overview,
        "preRequisites": case.prerequisites,
        "testObjective": case.objective,
        "expectedResult": case.expected_result,
        "steps": [{k: v for k, v in step.items() if k != "dataKey"} for step in (case.steps or [])],  # the AI sees the steps exactly as before
    }

    def converse(messages, max_output_tokens):
        return llm.converse(provider, model, system_prompt, messages, max_output_tokens)

    usage: dict = {}
    started = time.monotonic()
    try:
        out = tsg.run_generation(
            converse,
            test_case=engine_case,
            app_map=scan.app_map,
            set_id=str(case.set_id),
            system_prompt=system_prompt,
            on_progress=lambda text: jobs.set_progress(db, job.id, f"{text} ({label} {model})"),
            usage=usage,
        )
    except tsg.GenerationError as err:
        jobs.add_usage(db, job.id, usage.get("tokens_in"), usage.get("tokens_out"))
        if err.retryable:
            raise Retryable(err.message) from None
        # The rejected answers are kept on the job so they can be inspected (Pilot 1 saved them as .failed.json).
        raise JobFailed(err.message, result={"validation_issues": err.issues, "rejected_answers": err.attempts}) from None
    duration = int(time.monotonic() - started)

    jobs.set_progress(db, job.id, "Saving the script")
    script = db.scalar(select(TsScript).where(TsScript.test_case_id == case.id))
    if script is None:
        # One script number sequence per project; the project row lock keeps concurrent jobs from clashing.
        db.execute(select(Project.id).where(Project.id == job.project_id).with_for_update())
        base = db.scalar(select(func.coalesce(func.max(TsScript.seq), 0)).where(TsScript.project_id == job.project_id))
        script = TsScript(tenant_id=job.tenant_id, project_id=job.project_id, test_case_id=case.id, seq=base + 1, created_by=job.created_by)
        db.add(script)
    # Data-driven: values typed into the steps become DATA["key"] lookups (the AI prompt and the generator are unchanged).
    steps_with_keys = datadriven.with_keys(case.steps or [])
    pz = datadriven.parameterize(out["script"], steps_with_keys)
    if pz["keys"] and tsg.check_syntax(pz["code"]):
        pz = {"code": out["script"], "keys": [], "gaps": []}  # never store a script that does not parse: keep the plain one
    if steps_with_keys != (case.steps or []):
        case.steps = steps_with_keys  # keys are metadata: no new version, so no script is flagged out of date
    script.code = pz["code"]
    script.data_keys = pz["keys"]
    script.data_gaps = pz["gaps"]
    script.language = "javascript"
    script.job_id = job.id
    script.provider, script.model, script.prompt_sha256 = provider, model, prompt_sha
    script.tokens_in, script.tokens_out = out["tokens_in"], out["tokens_out"]
    script.duration_seconds = duration
    script.attempts = out["attempts"]
    script.elements_used = out["elements_used"]
    script.steps_without_locator = out["steps_without_locator"]
    script.not_automated = out["not_automated"]
    script.blocked_message = out["blocked_message"]
    script.case_version = case_version
    script.updated_at = datetime.now(timezone.utc)
    db.flush()
    jobs.succeed(
        db,
        job.id,
        {
            "script_id": str(script.id),
            "test_case_id": str(case.id),
            "script_key": f"TS-{script.seq:03d}",
            "blocked": bool(out["blocked_message"]),
            "not_automated": len(out["not_automated"]),
            "prompt_sha256": prompt_sha,
        },
        provider=provider,
        model=model,
        tokens_in=out["tokens_in"],
        tokens_out=out["tokens_out"],
        duration_ms=duration * 1000,
    )


HANDLERS = {"generate_test_cases": generate_test_cases, "generate_test_script": generate_test_script}
