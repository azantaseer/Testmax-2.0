"""Delete requirements, test cases and test scripts, one or many at a time. Always tenant- and project-scoped.

What goes with an item (the database cascades, nothing else is left behind):
- requirement  -> its scans, test case sets, test cases, test scripts, executions (runs, steps, screenshots)
- test case    -> its test script and that script's executions; a test case set that became empty
- test script  -> its executions (the test case stays and can get a new script) and its places in test batches (the batch stays, one script shorter)

An item that still has a queued or running job (generating, scanning, executing) is NOT deleted: it is reported back as
"blocked" with the reason, so a running job never ends up pointing at a missing row. That includes a running test batch that contains the script.
"""
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.deps import scoped_select
from app.models import User
from app.models_runner import Scan
from app.models_tests import TcCase, TcSet, TsScript
from app.models_work import Job, Requirement

ACTIVE = ("queued", "running")
BUSY_REASON = "A background task for it is still running (generating, scanning or executing). Wait until it ends or cancel it, then delete."


class DeleteMany(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


def _busy(db: Session, user: User, project_id: uuid.UUID, job_types: tuple, payload_key: str, ids) -> set:
    """The ids (as strings) of the given kind that have a queued or running job of one of the types."""
    wanted = [str(i) for i in ids]
    if not wanted:
        return set()
    rows = db.scalars(
        scoped_select(Job, user).where(
            Job.project_id == project_id, Job.type.in_(job_types), Job.status.in_(ACTIVE), Job.payload[payload_key].astext.in_(wanted)
        )
    ).all()
    return {str(j.payload.get(payload_key)) for j in rows}


def _busy_in_batch(db: Session, user: User, project_id: uuid.UUID, payload_key: str, ids) -> set:
    """The ids (as strings) that are part of a queued or running test batch run (the batch job lists them under `payload_key`)."""
    wanted = [str(i) for i in ids]
    if not wanted:
        return set()
    rows = db.scalars(
        scoped_select(Job, user).where(
            Job.project_id == project_id,
            Job.type == "run_script",
            Job.status.in_(ACTIVE),
            Job.payload["batch_id"].astext.is_not(None),
            or_(*[Job.payload[payload_key].contains([w]) for w in wanted]),
        )
    ).all()
    found = set()
    for j in rows:
        found |= {str(x) for x in (j.payload.get(payload_key) or [])} & set(wanted)
    return found


def _result(deleted: list, blocked: list, asked: int) -> dict:
    return {"deleted": deleted, "blocked": blocked, "missing": max(asked - len(deleted) - len(blocked), 0)}


def delete_requirements(db: Session, user: User, project_id: uuid.UUID, ids: list) -> dict:
    ids = list(dict.fromkeys(ids))
    reqs = db.scalars(scoped_select(Requirement, user).where(Requirement.id.in_(ids), Requirement.project_id == project_id).order_by(Requirement.seq)).all()
    req_ids = [r.id for r in reqs]
    cases = db.execute(scoped_select(TcCase, user).where(TcCase.requirement_id.in_(req_ids)).with_only_columns(TcCase.id, TcCase.requirement_id)).all() if req_ids else []
    case_ids = [c.id for c in cases]
    scripts = db.execute(scoped_select(TsScript, user).where(TsScript.test_case_id.in_(case_ids)).with_only_columns(TsScript.id, TsScript.test_case_id)).all() if case_ids else []
    busy_reqs = _busy(db, user, project_id, ("generate_test_cases", "app_scan"), "requirement_id", req_ids)
    busy_cases = _busy(db, user, project_id, ("generate_test_script", "run_script"), "test_case_id", case_ids)
    busy_scripts = _busy(db, user, project_id, ("run_script",), "script_id", [s.id for s in scripts]) | _busy_in_batch(db, user, project_id, "script_ids", [s.id for s in scripts])
    busy_by_case = {c.requirement_id for c in cases if str(c.id) in busy_cases}
    busy_by_case |= {c.requirement_id for c in cases for s in scripts if s.test_case_id == c.id and str(s.id) in busy_scripts}
    deleted, blocked = [], []
    for r in reqs:
        info = {"id": str(r.id), "key": f"REQ-{r.seq:03d}"}
        if str(r.id) in busy_reqs or r.id in busy_by_case:
            blocked.append({**info, "reason": BUSY_REASON})
            continue
        db.execute(delete(Scan).where(Scan.tenant_id == user.tenant_id, Scan.requirement_id == r.id))  # scans keep only SET NULL; they belong to the requirement
        db.delete(r)
        deleted.append(info)
    db.commit()
    return _result(deleted, blocked, len(ids))


def delete_test_cases(db: Session, user: User, project_id: uuid.UUID, ids: list) -> dict:
    ids = list(dict.fromkeys(ids))
    cases = db.scalars(scoped_select(TcCase, user).where(TcCase.id.in_(ids), TcCase.project_id == project_id).order_by(TcCase.seq)).all()
    case_ids = [c.id for c in cases]
    scripts = db.execute(scoped_select(TsScript, user).where(TsScript.test_case_id.in_(case_ids)).with_only_columns(TsScript.id, TsScript.test_case_id)).all() if case_ids else []
    busy = _busy(db, user, project_id, ("generate_test_script", "run_script"), "test_case_id", case_ids)
    busy_scripts = _busy(db, user, project_id, ("run_script",), "script_id", [s.id for s in scripts]) | _busy_in_batch(db, user, project_id, "script_ids", [s.id for s in scripts])
    busy |= {str(s.test_case_id) for s in scripts if str(s.id) in busy_scripts}
    deleted, blocked, set_ids = [], [], set()
    for c in cases:
        info = {"id": str(c.id), "key": f"TC-{c.seq:03d}"}
        if str(c.id) in busy:
            blocked.append({**info, "reason": BUSY_REASON})
            continue
        set_ids.add(c.set_id)
        db.delete(c)
        deleted.append(info)
    db.flush()
    if set_ids:  # a generation whose cases were all deleted is no longer listed
        still_used = select(TcCase.set_id).where(TcCase.set_id.in_(set_ids))
        db.execute(delete(TcSet).where(TcSet.tenant_id == user.tenant_id, TcSet.id.in_(set_ids), TcSet.id.not_in(still_used)))
    db.commit()
    return _result(deleted, blocked, len(ids))


def delete_test_scripts(db: Session, user: User, project_id: uuid.UUID, ids: list) -> dict:
    ids = list(dict.fromkeys(ids))
    scripts = db.scalars(scoped_select(TsScript, user).where(TsScript.id.in_(ids), TsScript.project_id == project_id).order_by(TsScript.seq)).all()
    busy = _busy(db, user, project_id, ("run_script",), "script_id", [s.id for s in scripts]) | _busy_in_batch(db, user, project_id, "script_ids", [s.id for s in scripts])
    busy_cases = _busy(db, user, project_id, ("generate_test_script",), "test_case_id", [s.test_case_id for s in scripts])
    deleted, blocked = [], []
    for s in scripts:
        info = {"id": str(s.id), "key": f"TS-{s.seq:03d}"}
        if str(s.id) in busy or str(s.test_case_id) in busy_cases:
            blocked.append({**info, "reason": BUSY_REASON})
            continue
        db.delete(s)
        deleted.append(info)
    db.commit()
    return _result(deleted, blocked, len(ids))
