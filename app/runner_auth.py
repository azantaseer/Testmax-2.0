"""Authentication for runners: Authorization: Bearer <runner key>. Only the key's hash is stored."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError
from app.models_runner import Runner

KEY_PREFIX = "tmr_"
ONLINE_SECONDS = 30  # a runner heartbeats every ~10 s; silent for 30 s means offline


def new_runner_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def is_online(runner: Runner) -> bool:
    return bool(runner.last_seen_at and runner.last_seen_at > datetime.now(timezone.utc) - timedelta(seconds=ONLINE_SECONDS))


def current_runner(request: Request, db: Session = Depends(get_db)) -> Runner:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise AppError(401, "runner_not_authenticated", "Runner key missing.")
    key = header[7:].strip()
    runner = db.scalar(select(Runner).where(Runner.key_hash == hash_key(key)))
    if runner is None:
        raise AppError(401, "runner_not_authenticated", "Runner key not recognised.")
    if runner.revoked_at is not None:
        raise AppError(401, "runner_revoked", "This runner was revoked by an administrator.")
    now = datetime.now(timezone.utc)
    if runner.last_seen_at is None or (now - runner.last_seen_at).total_seconds() > 5:
        db.execute(update(Runner).where(Runner.id == runner.id).values(last_seen_at=now))
        db.commit()
        db.refresh(runner)
    return runner
