"""Job handlers: what each job type does. Handlers raise JobFailed (give up) or Retryable (try again if attempts remain)."""
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import jobs, llm, providers
from app.engines import requirements as req_engine
from app.models import Project
from app.models_work import Document, Job, Requirement



class JobFailed(Exception):
    """Do not retry; the message is shown to the user. `result` (optional) is kept on the job for inspection."""

    def __init__(self, message: str, result: dict | None = None):
        super().__init__(message)
        self.result = result


class Retryable(Exception):
    """Transient problem; retry if attempts remain."""


def generate_requirements(db: Session, job: Job) -> None:
    provider = job.payload.get("provider", "")
    model = job.payload.get("model", "")
    document = db.get(Document, job.payload.get("document_id"))
    if document is None or document.tenant_id != job.tenant_id or document.project_id != job.project_id:
        raise JobFailed("The uploaded document is no longer available.")
    if db.get(Project, job.project_id) is None:
        raise JobFailed("The project no longer exists.")

    jobs.set_progress(db, job.id, "Reading the document")
    system_prompt, prompt_sha = req_engine.load_system_prompt()

    label = providers.get(provider).label if providers.get(provider) else provider
    jobs.set_progress(db, job.id, f"Asking {label} ({model}) to extract requirements")
    started = time.monotonic()
    try:
        result = llm.complete(provider, model, system_prompt, document.text)
    except llm.LLMError as err:
        raise (Retryable(err.message) if err.retryable else JobFailed(err.message)) from None
    duration_ms = int((time.monotonic() - started) * 1000)

    jobs.set_progress(db, job.id, "Checking and saving the requirements")
    try:
        items = req_engine.parse_reply(result.text)
    except ValueError as err:
        raise Retryable(str(err)) from None
    if not items:
        raise JobFailed("No requirements were found in this document.")

    # One requirement number sequence per project; the project row lock keeps concurrent jobs from clashing.
    db.execute(select(Project.id).where(Project.id == job.project_id).with_for_update())
    base = db.scalar(select(func.coalesce(func.max(Requirement.seq), 0)).where(Requirement.project_id == job.project_id))
    db.add_all(
        Requirement(
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            job_id=job.id,
            document_id=document.id,
            seq=base + index,
            summary=item["summary"],
            description=item["description"],
            acceptance_criteria=item["acceptanceCriteria"],
            provider=provider,
            model=model,
        )
        for index, item in enumerate(items, start=1)
    )
    # Requirements and the job's success are saved in the same transaction.
    jobs.succeed(
        db,
        job.id,
        {"requirement_count": len(items), "first_seq": base + 1, "prompt_sha256": prompt_sha},
        provider=provider,
        model=model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        duration_ms=duration_ms,
    )


HANDLERS = {"generate_requirements": generate_requirements}


from app import handlers_tests  # noqa: E402  (test case and script handlers)

HANDLERS.update(handlers_tests.HANDLERS)
