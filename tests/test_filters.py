"""Filters on the requirements list (used by the Filter popover): scan state, model, created day range; combined with search."""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models_work import Requirement
from tests.conftest import new_client, signup_org
from tests.test_tcg import seed


def keys(c, pid, query=""):
    return [r["key"] for r in c.get(f"/api/projects/{pid}/requirements{query}").json()["requirements"]]


def test_requirement_filters(monkeypatch, db):
    c, pid, _req, _scan = seed(monkeypatch, db)  # REQ-001 has a scan, REQ-002 has none
    assert keys(c, pid) == ["REQ-001", "REQ-002"]  # no filter: unchanged behaviour

    assert keys(c, pid, "?scan=scanned") == ["REQ-001"]
    assert keys(c, pid, "?scan=not_scanned") == ["REQ-002"]
    assert keys(c, pid, "?scan=scanned&scan=not_scanned") == ["REQ-001", "REQ-002"]
    bad = c.get(f"/api/projects/{pid}/requirements?scan=maybe")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_filter"

    rows = db.scalars(select(Requirement).where(Requirement.project_id == pid).order_by(Requirement.seq)).all()
    rows[0].created_at = datetime(2026, 1, 5, 23, 30, tzinfo=timezone.utc)
    rows[1].model = "other-model"
    db.commit()
    body = c.get(f"/api/projects/{pid}/requirements").json()
    assert body["models"] == ["other-model", "system-default-model"] and body["total"] == 2
    assert keys(c, pid, "?model=other-model") == ["REQ-002"]
    assert keys(c, pid, "?model=other-model&model=system-default-model") == ["REQ-001", "REQ-002"]

    assert keys(c, pid, "?created_from=2026-01-05&created_to=2026-01-05") == ["REQ-001"]  # both ends are inclusive days
    assert keys(c, pid, "?created_to=2026-01-04") == []
    assert keys(c, pid, "?created_from=2026-01-06&created_to=2026-01-07") == []
    assert "REQ-001" not in keys(c, pid, "?created_from=2026-01-06")
    assert c.get(f"/api/projects/{pid}/requirements?created_from=not-a-date").status_code == 422

    assert keys(c, pid, "?scan=not_scanned&model=other-model") == ["REQ-002"]  # filters combine
    assert keys(c, pid, "?scan=scanned&model=other-model") == []
    total = c.get(f"/api/projects/{pid}/requirements?scan=not_scanned&page_size=1").json()
    assert total["total"] == 1  # the total counts the filtered list, so the pager is right


def test_filters_and_models_never_cross_organizations(monkeypatch, db):
    c, pid, *_ = seed(monkeypatch, db)
    other = new_client()
    assert signup_org(other, org="Other Co", email="boss@other.example").status_code == 201
    opid = other.post("/api/projects", json={"name": "Theirs"}).json()["id"]
    assert other.get(f"/api/projects/{pid}/requirements?scan=scanned").status_code == 404
    assert other.get(f"/api/projects/{opid}/requirements").json()["models"] == []


# ---- test cases and test scripts ---------------------------------------------
def case_keys(c, pid, query=""):
    return [x["key"] for x in c.get(f"/api/projects/{pid}/test-cases{query}").json()["test_cases"]]


def script_keys(c, pid, query=""):
    return [x["key"] for x in c.get(f"/api/projects/{pid}/test-scripts{query}").json()["scripts"]]


def test_test_case_and_script_filters(monkeypatch, db):
    from tests.test_execute import make_script

    c, pid, script, ids = make_script(monkeypatch, db)  # TC-001 has a script, TC-002 has none
    assert case_keys(c, pid) == ["TC-001", "TC-002"]

    assert case_keys(c, pid, "?script=with_script") == ["TC-001"]
    assert case_keys(c, pid, "?script=without_script") == ["TC-002"]
    assert case_keys(c, pid, "?script=with_script&script=without_script") == ["TC-001", "TC-002"]
    assert case_keys(c, pid, "?script=out_of_date") == []
    assert script_keys(c, pid, "?state=up_to_date") == ["TS-001"]
    assert script_keys(c, pid, "?state=out_of_date") == []

    # editing the case makes its script out of date
    r = c.patch(f"/api/projects/{pid}/test-cases/{ids['TC-001']}", json={"version": 1, "title": "Renamed by a person"})
    assert r.status_code == 200
    assert case_keys(c, pid, "?script=out_of_date") == ["TC-001"]
    assert script_keys(c, pid, "?state=out_of_date") == ["TS-001"]
    assert script_keys(c, pid, "?state=up_to_date") == []
    assert script_keys(c, pid, "?state=blocked") == []

    one = c.get(f"/api/projects/{pid}/test-cases").json()["test_cases"][0]
    assert case_keys(c, pid, f"?type={one['type']}")[0] == "TC-001"
    assert case_keys(c, pid, f"?type={one['type'].lower()}")[0] == "TC-001"  # case does not matter
    other = "NEGATIVE" if one["type"] != "NEGATIVE" else "POSITIVE"
    assert "TC-001" not in case_keys(c, pid, f"?type={other}")
    assert script_keys(c, pid, f"?type={one['type']}") == ["TS-001"]
    assert script_keys(c, pid, f"?type={other}") == []

    req_id = one["requirement_id"] if "requirement_id" in one else None
    if req_id:
        assert case_keys(c, pid, f"?requirement_id={req_id}") == ["TC-001", "TC-002"]  # the old single-requirement call still works
    assert case_keys(c, pid, "?requirement_id=00000000-0000-0000-0000-000000000000") == []

    assert case_keys(c, pid, "?created_to=2000-01-01") == [] and script_keys(c, pid, "?created_to=2000-01-01") == []
    assert case_keys(c, pid, "?created_from=2000-01-01") == ["TC-001", "TC-002"]
    for bad in ("?type=ODD", "?script=maybe", "?created_from=x"):
        assert c.get(f"/api/projects/{pid}/test-cases{bad}").status_code == 422
    for bad in ("?type=ODD", "?state=maybe", "?created_to=x"):
        assert c.get(f"/api/projects/{pid}/test-scripts{bad}").status_code == 422
    assert c.get(f"/api/projects/{pid}/test-cases?script=without_script&page_size=1").json()["total"] == 1
