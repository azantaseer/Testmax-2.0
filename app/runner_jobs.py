"""Job types that run on a runner (not on server workers): what the runner is given, and how its result is saved."""
from sqlalchemy.orm import Session

from app import jobs
from app.config import get_settings
from app.models_runner import Runner
from app.models_work import Job

RUNNER_JOB_TYPES = ["app_scan", "runner_stub", "run_script"]


def claim_payload(job: Job) -> dict:
    s = get_settings()
    out = {
        "id": str(job.id),
        "type": job.type,
        "payload": dict(job.payload),
        "attempts": job.attempts,
        "lease_seconds": s.job_lease_seconds,
        "heartbeat_seconds": s.job_heartbeat_seconds,
    }
    extra = EXTRAS.get(job.type)
    if extra:
        out.update(extra(job))
    return out


def _complete_stub(db: Session, job: Job, runner: Runner, result: dict) -> None:
    jobs.succeed(db, job.id, {"echo": result.get("echo"), "runner": runner.name})


COMPLETERS = {"runner_stub": _complete_stub}
EXTRAS: dict = {}

# System prompt per job type for the model proxy. The runner never chooses or sends a prompt.
SYSTEM_PROMPTS: dict = {}


from app import run_service, scan_service  # noqa: E402,F401  (register the app_scan and run_script completers)
