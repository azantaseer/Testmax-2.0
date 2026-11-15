"""Editing requirements and test cases (and the steps of a test case) by hand.

- Every edit carries the `version` the person was looking at; if somebody saved in between, the edit is refused (409) so
  nobody silently overwrites a colleague.
- Text is trimmed, limited in length and stripped of control characters. Nothing is executed or rendered as HTML.
- A test case step keeps its scanned source control (`sourceElementId`) only when that step already had it; a new or
  re-pointed step never gets one, so a person cannot make a script click a control the scan never saw.
- Steps are renumbered 1..n. Pilot 1's rules for a step (starts with an approved action verb, one action per step) are
  applied as warnings, not refusals: the person decides, the script generator copes with what it gets.
- Editing a test case does not touch its script; the script is reported as out of date (its `case_version` differs) and
  the person decides whether to regenerate it (that costs an AI call).
"""
import re
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import scoped_select
from app.engines import datadriven, tcg
from app.errors import AppError
from app.models import User
from app.models_tests import TcCase, TsScript
from app.models_work import Requirement

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EMPTY = "—"
MAX_STEPS = 100


def clean(text: str) -> str:
    return CONTROL_CHARS.sub("", text).replace("\r\n", "\n").strip()


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", clean(text))


class RequirementEdit(BaseModel):
    version: int
    summary: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=60000)
    acceptance_criteria: str | None = Field(default=None, max_length=60000)


class StepEdit(BaseModel):
    testStep: str = Field(max_length=1000)
    fieldName: str = Field(default="", max_length=200)
    testData: str = Field(default="", max_length=500)
    expectedResult: str = Field(default="", max_length=1000)
    sourceElementId: str | None = Field(default=None, max_length=200)
    dataKey: str | None = Field(default=None, max_length=60)


class TestCaseEdit(BaseModel):
    __test__ = False  # not a pytest class
    version: int
    title: str | None = Field(default=None, max_length=300)
    type: str | None = None
    overview: str | None = Field(default=None, max_length=5000)
    prerequisites: list[str] | None = Field(default=None, max_length=30)
    objective: str | None = Field(default=None, max_length=2000)
    expected_result: str | None = Field(default=None, max_length=2000)
    steps: list[StepEdit] | None = Field(default=None, max_length=MAX_STEPS)

    @field_validator("prerequisites")
    @classmethod
    def _short_items(cls, items):
        if items is not None and any(len(i) > 500 for i in items):
            raise ValueError("A prerequisite is longer than 500 characters.")
        return items


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _touch(row, user: User) -> None:
    row.version = (row.version or 1) + 1
    row.updated_at = _now()
    row.updated_by = user.id
    row.edited = True


def _check_version(row, version: int, what: str) -> None:
    if row.version != version:
        raise AppError(409, "edit_conflict", f"This {what} was changed by someone else while you were editing. Reload the page to see their version, then make your change again.")


def edit_requirement(db: Session, user: User, project_id: uuid.UUID, requirement_id: uuid.UUID, body: RequirementEdit) -> Requirement:
    req = db.scalar(scoped_select(Requirement, user).where(Requirement.id == requirement_id, Requirement.project_id == project_id).with_for_update())
    if req is None:
        raise AppError(404, "requirement_not_found", "Requirement not found.")
    _check_version(req, body.version, "requirement")
    changed = False
    if body.summary is not None:
        summary = _one_line(body.summary)
        if not summary:
            raise AppError(422, "invalid_edit", "The summary cannot be empty.")
        changed |= summary != req.summary
        req.summary = summary
    if body.description is not None:
        description = clean(body.description)
        if not description:
            raise AppError(422, "invalid_edit", "The description cannot be empty.")
        changed |= description != req.description
        req.description = description
    if body.acceptance_criteria is not None:
        criteria = clean(body.acceptance_criteria)
        changed |= criteria != req.acceptance_criteria
        req.acceptance_criteria = criteria
    if changed:
        _touch(req, user)
    db.commit()
    return req


def step_warnings(steps: list[dict]) -> list[str]:
    """Pilot 1's rules for a step, as advice (the person may keep the step as written)."""
    out = []
    for step in steps:
        text = step["testStep"]
        first = text.split(" ", 1)[0].strip(".,:;").lower()
        if first not in tcg.APPROVED_ACTIONS:
            out.append(f"Step {step['stepNo']} does not start with an approved action ({', '.join(v.capitalize() for v in tcg.APPROVED_ACTIONS)}); the script writer may not understand it.")
        elif tcg._has_multiple_actions(text):
            out.append(f"Step {step['stepNo']} seems to combine more than one action; one action per step gives better scripts.")
    return out


def _new_steps(old_steps: list[dict], edits: list[StepEdit]) -> list[dict]:
    if not edits:
        raise AppError(422, "invalid_edit", "A test case needs at least one step.")
    known = {s.get("sourceElementId") for s in old_steps if s.get("sourceElementId")}
    known_keys = {s.get("dataKey") for s in datadriven.with_keys(old_steps) if s.get("dataKey")}
    steps = []
    for number, edit in enumerate(edits, start=1):
        text = _one_line(edit.testStep)
        if not text:
            raise AppError(422, "invalid_edit", f"Step {number} has no text. Write the action, or delete the step.")
        step = {
            "stepNo": number,
            "testStep": text,
            "fieldName": _one_line(edit.fieldName) or EMPTY,
            "testData": clean(edit.testData) or EMPTY,
            "expectedResult": _one_line(edit.expectedResult) or EMPTY,
            # the same shape the generator stores: the id, or None for a step without a scanned control
            "sourceElementId": edit.sourceElementId if edit.sourceElementId and edit.sourceElementId in known else None,
        }
        # a step keeps its test data key (so data variations stay attached to it); a new step gets a fresh one below
        if edit.dataKey and edit.dataKey in known_keys:
            step["dataKey"] = edit.dataKey
        steps.append(step)
    seen = set()
    for step in steps:  # a key is used by one step only
        if step.get("dataKey") in seen:
            step.pop("dataKey")
        elif step.get("dataKey"):
            seen.add(step["dataKey"])
    return datadriven.with_keys(steps)


def _without_keys(steps: list) -> list:
    return [{k: v for k, v in step.items() if k != "dataKey"} for step in (steps or [])]


def _keep_script_current_for_data_edit(db: Session, case: TcCase, old_version: int, old_keyed: list, before: tuple, after: tuple) -> None:
    """Changing only the test data that a data-driven script reads from its data block does not make the script out of date:
    the script is not changed by it (the values are filled in when it runs). Anything else still does."""
    script = db.scalar(select(TsScript).where(TsScript.test_case_id == case.id, TsScript.tenant_id == case.tenant_id))
    if script is None or not script.data_keys or script.case_version != old_version:
        return
    if before[:6] != after[:6] or not datadriven.only_data_changed(old_keyed, case.steps or [], list(script.data_keys)):
        return
    script.case_version = case.version
    script.code = datadriven.rewrite_block(script.code, datadriven.values_for(case.steps or [], None))


def edit_test_case(db: Session, user: User, project_id: uuid.UUID, case_id: uuid.UUID, body: TestCaseEdit) -> tuple[TcCase, list[str]]:
    case = db.scalar(scoped_select(TcCase, user).where(TcCase.id == case_id, TcCase.project_id == project_id).with_for_update())
    if case is None:
        raise AppError(404, "test_case_not_found", "Test case not found.")
    _check_version(case, body.version, "test case")
    old_version = case.version
    old_keyed = datadriven.with_keys(case.steps or [])
    before = (case.title, case.case_type, case.overview, list(case.prerequisites or []), case.objective, case.expected_result, _without_keys(case.steps))
    if body.title is not None:
        title = _one_line(body.title)
        if not title:
            raise AppError(422, "invalid_edit", "The title cannot be empty.")
        case.title = title
    if body.type is not None:
        kind = body.type.strip().upper()
        if kind not in tcg.TESTCASE_TYPES:
            raise AppError(422, "invalid_edit", f"The type must be one of {', '.join(sorted(tcg.TESTCASE_TYPES))}.")
        case.case_type = kind
    if body.overview is not None:
        case.overview = clean(body.overview)
    if body.prerequisites is not None:
        case.prerequisites = [_one_line(p) for p in body.prerequisites if _one_line(p)]
    if body.objective is not None:
        case.objective = clean(body.objective)
    if body.expected_result is not None:
        result = clean(body.expected_result)
        if not result:
            raise AppError(422, "invalid_edit", "The expected result cannot be empty.")
        case.expected_result = result
    if body.steps is not None:
        case.steps = _new_steps(case.steps or [], body.steps)
    elif old_keyed != (case.steps or []):
        case.steps = old_keyed  # remember the keys; this is not a change a person made
    after = (case.title, case.case_type, case.overview, list(case.prerequisites or []), case.objective, case.expected_result, _without_keys(case.steps))
    if after != before:
        _touch(case, user)
        _keep_script_current_for_data_edit(db, case, old_version, old_keyed, before, after)
    db.commit()
    return case, step_warnings(case.steps or []) if body.steps is not None else []
