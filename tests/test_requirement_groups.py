"""Grouping requirements: the source document (filter, facet) and free-form tags (bulk add/remove, filter, facet)."""
from tests.conftest import new_client, signup_org
from tests.test_requirements import BRD, fake_llm, org, project, run_next, upload


def listing(c, pid, query=""):
    return c.get(f"/api/projects/{pid}/requirements{query}").json()


def keys(c, pid, query=""):
    return [r["key"] for r in listing(c, pid, query)["requirements"]]


def two_documents(monkeypatch):
    """REQ-001, REQ-002 come from login.txt; REQ-003, REQ-004 from patients.txt."""
    c = org()
    pid = project(c)
    fake_llm(monkeypatch)
    assert upload(c, pid, BRD, "login.txt").status_code == 202
    run_next()
    assert upload(c, pid, BRD, "patients.txt").status_code == 202
    run_next()
    return c, pid, listing(c, pid)["requirements"]


def test_every_requirement_shows_its_source_document_and_can_be_filtered_by_it(monkeypatch):
    c, pid, reqs = two_documents(monkeypatch)
    assert [r["source"]["filename"] for r in reqs] == ["login.txt", "login.txt", "patients.txt", "patients.txt"]
    body = listing(c, pid)
    facet = {d["filename"]: d for d in body["documents"]}
    assert set(facet) == {"login.txt", "patients.txt"} and facet["login.txt"]["count"] == 2 and facet["patients.txt"]["count"] == 2
    assert keys(c, pid, f"?document={facet['patients.txt']['id']}") == ["REQ-003", "REQ-004"]
    both = f"?document={facet['patients.txt']['id']}&document={facet['login.txt']['id']}"
    assert keys(c, pid, both) == ["REQ-001", "REQ-002", "REQ-003", "REQ-004"]
    assert keys(c, pid, "?document=00000000-0000-0000-0000-000000000000") == []
    assert c.get(f"/api/projects/{pid}/requirements?document=nope").status_code == 422
    one = c.get(f"/api/projects/{pid}/requirements/{reqs[2]['id']}").json()
    assert one["source"]["filename"] == "patients.txt" and one["tags"] == []  # the requirement page gets it too


def test_tags_are_added_removed_and_filtered(monkeypatch):
    c, pid, reqs = two_documents(monkeypatch)
    ids = [r["id"] for r in reqs]
    url = f"/api/projects/{pid}/requirements/tags"
    done = c.post(url, json={"ids": ids[:2], "add": ["Login", "  Smoke   test "]})
    assert done.status_code == 200 and done.json()["updated"] == 2
    assert {t["name"]: t["count"] for t in done.json()["tags"]} == {"Login": 2, "Smoke test": 2}
    assert [r["tags"] for r in listing(c, pid)["requirements"]][:2] == [["Login", "Smoke test"], ["Login", "Smoke test"]]
    # an existing spelling is reused: "login" does not become a second tag
    c.post(url, json={"ids": [ids[2]], "add": ["login"]})
    assert listing(c, pid)["requirements"][2]["tags"] == ["Login"]
    assert {t["name"]: t["count"] for t in listing(c, pid)["tags"]} == {"Login": 3, "Smoke test": 2}
    # filter: any of the chosen tags; combines with the other filters
    assert keys(c, pid, "?tag=Smoke test") == ["REQ-001", "REQ-002"]
    assert keys(c, pid, "?tag=Smoke test&tag=Login") == ["REQ-001", "REQ-002", "REQ-003"]
    facet = {d["filename"]: d["id"] for d in listing(c, pid)["documents"]}
    assert keys(c, pid, f"?tag=Login&document={facet['patients.txt']}") == ["REQ-003"]
    assert listing(c, pid, "?tag=Smoke test&page_size=1")["total"] == 2
    # removing is case-insensitive; adding something already there changes nothing
    again = c.post(url, json={"ids": ids[:2], "add": ["LOGIN"], "remove": ["smoke TEST"]})
    assert again.json()["updated"] == 2
    assert keys(c, pid, "?tag=Smoke test") == []
    assert c.post(url, json={"ids": ids[:2], "add": ["Login"]}).json()["updated"] == 0


def test_tagging_changes_nothing_else_about_the_requirement(monkeypatch):
    c, pid, reqs = two_documents(monkeypatch)
    before = c.get(f"/api/projects/{pid}/requirements/{reqs[0]['id']}").json()
    c.post(f"/api/projects/{pid}/requirements/tags", json={"ids": [reqs[0]["id"]], "add": ["Login"]})
    after = c.get(f"/api/projects/{pid}/requirements/{reqs[0]['id']}").json()
    assert after["version"] == before["version"] == 1 and after["edited"] is False and after["updated_at"] == before["updated_at"]
    assert after["tags"] == ["Login"] and after["summary"] == before["summary"]


def test_tag_rules_and_isolation(monkeypatch):
    c, pid, reqs = two_documents(monkeypatch)
    ids = [r["id"] for r in reqs]
    url = f"/api/projects/{pid}/requirements/tags"
    assert c.post(url, json={"ids": ids[:1], "add": ["x" * 31]}).json()["error"]["code"] == "invalid_tag"
    assert c.post(url, json={"ids": [], "add": ["a"]}).status_code == 422
    assert c.post(url, json={"ids": ids[:1], "add": [str(n) for n in range(11)]}).status_code == 422  # more than 10 in one request
    c.post(url, json={"ids": ids[:1], "add": [f"t{n}" for n in range(10)]})
    too_many = c.post(url, json={"ids": ids[:2], "add": ["eleventh"]})  # REQ-001 is full: nothing is changed for either
    assert too_many.status_code == 422 and too_many.json()["error"]["code"] == "too_many_tags" and "REQ-001" in too_many.json()["error"]["message"]
    assert "eleventh" not in [t["name"] for t in listing(c, pid)["tags"]]
    assert c.post(url, json={"ids": ["00000000-0000-0000-0000-000000000000"], "add": ["a"]}).status_code == 404
    other = new_client()
    assert signup_org(other, org="Other Co", email="boss@other.example").status_code == 201
    opid = project(other, "Theirs")
    assert other.post(f"/api/projects/{pid}/requirements/tags", json={"ids": ids[:1], "add": ["spy"]}).status_code == 404  # not their project
    assert other.post(f"/api/projects/{opid}/requirements/tags", json={"ids": ids[:1], "add": ["spy"]}).status_code == 404  # not their requirement
    assert listing(other, opid)["tags"] == [] and listing(other, opid)["documents"] == []
