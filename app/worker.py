"""Background worker: python -m app.worker

Claims jobs from Postgres, runs them, renews their leases, and recovers jobs of dead workers.
Run several copies (docker compose up --scale worker=N) for more throughput.
"""
import logging
import os
import signal
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app import jobs
from app.config import get_settings
from app.db import get_engine
from app.handlers import HANDLERS, JobFailed, Retryable
from app.models_work import Job

log = logging.getLogger("worker")


def new_session() -> Session:
    return Session(get_engine(), expire_on_commit=False)


def run_job(job_id: uuid.UUID, worker_id: str) -> str:
    """Run one claimed job. Returns the final state: succeeded, failed or requeued."""
    with new_session() as db:
        job = db.get(Job, job_id)
        handler = HANDLERS.get(job.type)
        try:
            if handler is None:
                raise JobFailed(f"Unknown job type '{job.type}'.")
            handler(db, job)
            return "succeeded"
        except JobFailed as err:
            db.rollback()
            jobs.fail(db, job_id, str(err), getattr(err, "result", None))
            return "failed"
        except Retryable as err:
            db.rollback()
            return jobs.retry_or_fail(db, job_id, str(err))
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("job %s crashed", job_id)  # traceback goes to the server log only
            jobs.fail(db, job_id, "An unexpected error occurred while running this job.")
            return "failed"


class Worker:
    def __init__(self, concurrency: int | None = None, worker_id: str | None = None):
        s = get_settings()
        self.concurrency = concurrency or s.worker_concurrency
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.stop = threading.Event()
        self.inflight: set[uuid.UUID] = set()
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="job")

    def _run(self, job_id: uuid.UUID) -> None:
        try:
            state = run_job(job_id, self.worker_id)
            log.info("job %s -> %s", job_id, state)
        finally:
            with self.lock:
                self.inflight.discard(job_id)

    def _heartbeat_loop(self) -> None:
        every = get_settings().job_heartbeat_seconds
        while not self.stop.wait(every):
            with self.lock:
                ids = list(self.inflight)
            try:
                with new_session() as db:
                    jobs.heartbeat(db, self.worker_id, ids)
            except Exception:  # noqa: BLE001
                log.exception("heartbeat failed")

    def step(self) -> bool:
        """Reap expired jobs, then claim and start one job if there is a free slot. True if a job was started."""
        with new_session() as db:
            recovered = jobs.reap_expired(db)
            if recovered["requeued"] or recovered["failed"]:
                log.warning("recovered jobs: requeued=%s failed=%s", recovered["requeued"], recovered["failed"])
        with self.lock:
            if len(self.inflight) >= self.concurrency:
                return False
        with new_session() as db:
            job_id = jobs.claim(db, self.worker_id, list(HANDLERS))
        if job_id is None:
            return False
        with self.lock:
            self.inflight.add(job_id)
        self.executor.submit(self._run, job_id)
        return True

    def serve(self) -> None:
        log.info("worker %s started (concurrency %s)", self.worker_id, self.concurrency)
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        while not self.stop.is_set():
            try:
                started = self.step()
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                started = False
            if not started:
                self.stop.wait(1.0)
        self.shutdown()

    def shutdown(self, grace_seconds: float = 8.0) -> None:
        """Clean stop: give running jobs a moment, then hand unfinished ones straight back to the queue."""
        log.info("worker %s stopping", self.worker_id)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            with self.lock:
                if not self.inflight:
                    break
            time.sleep(0.2)
        with self.lock:
            left = list(self.inflight)
        if left:
            with new_session() as db:
                jobs.release(db, self.worker_id, left)
            log.info("released %s unfinished job(s) back to the queue", len(left))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = Worker()

    def _stop(*_):
        worker.stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    worker.serve()
    os._exit(0)  # do not wait for threads stuck inside a provider call


if __name__ == "__main__":
    main()
