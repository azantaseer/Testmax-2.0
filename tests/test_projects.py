import time

from sqlalchemy import select

from app.models import Project
from tests.conftest import GOOD_PASSWORD, login, new_client, signup_org


def org(name="Acme Corp", email="owner@acme.example"):
    c = new_client()
    assert signup_org(c, org=name, email=email).status_code == 201
    return c


def add_user(owner, email, role="user"):
    r = owner.post("/api/users", json={"name": email.split("@")[0], "email": email, "password": GOOD_PASSWORD, "role": role})
    assert r.status_code == 201
    c = new_client()
    assert login(c, email).status_code == 200
    return c, r.json()["id"]


def mk(c, name, description="", member_ids=()):
    return c.post("/api/projects", json={"name": name, "description": description, "member_ids": list(member_ids)})


def test_create_read_list_project():
    owner = org()
    r = mk(owner, "  Web   App Project ", "Main site", [])
    assert r.status_code == 201
    p = r.json()
    assert p["name"] == "Web App Project" and p["description"] == "Main site" and p["assigned_users"] == [] and p["can_edit"] is True
    assert owner.get(f"/api/projects/{p['id']}").json()["name"] == "Web App Project"
    listing = owner.get("/api/projects").json()
    assert listing["total"] == 1 and listing["projects"][0]["id"] == p["id"]


def test_validation_and_duplicate_names_per_tenant():
    owner = org()
    assert owner.post("/api/projects", json={"name": "  "}).status_code == 422
    assert owner.post("/api/projects", json={"name": "x" * 101}).status_code == 422
    assert owner.post("/api/projects", json={"name": "ok", "description": "d" * 2001}).status_code == 422
    assert mk(owner, "Shop").status_code == 201
    r = mk(owner, "SHOP")
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_exists"
    # another tenant may reuse the name
    other = org("Other Co", "o@other.example")
    assert mk(other, "Shop").status_code == 201


def test_unauthenticated_is_rejected():
    c = new_client()
    assert c.get("/api/projects").status_code == 401
    assert c.post("/api/projects", json={"name": "x"}).status_code == 401
    assert c.get("/api/projects/assignable-users").status_code == 401


def test_search_sort_and_pagination():
    owner = org()
    for n in ["Beta", "Alpha", "Gamma", "Delta"]:
        mk(owner, n, f"about {n.lower()}")
        time.sleep(0.01)
    by_name = owner.get("/api/projects?sort=name&order=asc").json()["projects"]
    assert [p["name"] for p in by_name] == ["Alpha", "Beta", "Delta", "Gamma"]
    newest = owner.get("/api/projects?sort=updated&order=desc").json()["projects"]
    assert [p["name"] for p in newest][0] == "Delta"
    page2 = owner.get("/api/projects?sort=name&order=asc&page_size=3&page=2").json()
    assert page2["total"] == 4 and [p["name"] for p in page2["projects"]] == ["Gamma"]
    assert [p["name"] for p in owner.get("/api/projects?q=ABOUT%20gam").json()["projects"]] == ["Gamma"]
    assert owner.get("/api/projects?q=%25").json()["total"] == 0  # wildcard is escaped


def test_assigned_users_must_be_active_members_of_the_same_tenant():
    owner = org()
    other = org("Other Co", "o@other.example")
    _, member_id = add_user(owner, "m@acme.example")
    _, foreign_id = add_user(other, "f@other.example")
    ok = mk(owner, "P1", member_ids=[member_id, member_id])
    assert ok.status_code == 201 and [u["name"] for u in ok.json()["assigned_users"]] == ["m"]
    bad = mk(owner, "P2", member_ids=[foreign_id])
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_member"
    assert mk(owner, "P3", member_ids=["00000000-0000-0000-0000-000000000000"]).status_code == 400
    # a disabled member can no longer be assigned
    owner.patch(f"/api/users/{member_id}", json={"status": "disabled"})
    assert mk(owner, "P4", member_ids=[member_id]).status_code == 400


def test_assignable_users_lists_only_own_active_tenant_users():
    owner = org()
    other = org("Other Co", "o@other.example")
    add_user(owner, "m@acme.example")
    _, dis = add_user(owner, "d@acme.example")
    owner.patch(f"/api/users/{dis}", json={"status": "disabled"})
    add_user(other, "f@other.example")
    names = [u["name"] for u in owner.get("/api/projects/assignable-users").json()["users"]]
    assert names == ["m", "Olive Owner"]  # no disabled user, nobody from the other tenant


def test_update_project_fields_and_members():
    owner = org()
    _, m1 = add_user(owner, "m1@acme.example")
    _, m2 = add_user(owner, "m2@acme.example")
    p = mk(owner, "P", "d", [m1]).json()
    r = owner.patch(f"/api/projects/{p['id']}", json={"name": "Renamed", "description": "new", "member_ids": [m2]})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Renamed" and body["description"] == "new" and [u["name"] for u in body["assigned_users"]] == ["m2"]
    assert body["updated_at"] >= p["updated_at"]
    assert owner.patch(f"/api/projects/{p['id']}", json={"member_ids": []}).json()["assigned_users"] == []
    mk(owner, "Taken")
    assert owner.patch(f"/api/projects/{p['id']}", json={"name": "taken"}).status_code == 409
    assert owner.get(f"/api/projects/{p['id']}").json()["name"] == "Renamed"  # failed rename changed nothing


def test_only_creator_or_admin_can_edit_or_delete():
    owner = org()
    creator, _ = add_user(owner, "creator@acme.example")
    other, _ = add_user(owner, "other@acme.example")
    site, _ = add_user(owner, "site@acme.example", role="site_admin")
    p = mk(creator, "Mine").json()
    assert other.get(f"/api/projects/{p['id']}").json()["can_edit"] is False  # everyone in the tenant can see it
    assert other.patch(f"/api/projects/{p['id']}", json={"name": "Hijack"}).status_code == 403
    assert other.delete(f"/api/projects/{p['id']}").status_code == 403
    assert site.patch(f"/api/projects/{p['id']}", json={"description": "by admin"}).status_code == 200
    assert creator.delete(f"/api/projects/{p['id']}").status_code == 204
    assert owner.get(f"/api/projects/{p['id']}").status_code == 404


def test_delete_removes_project_and_members(db):
    owner = org()
    _, m = add_user(owner, "m@acme.example")
    p = mk(owner, "Gone", member_ids=[m]).json()
    assert owner.delete(f"/api/projects/{p['id']}").status_code == 204
    assert owner.get("/api/projects").json()["total"] == 0
    assert db.scalar(select(Project.id)) is None


def test_projects_are_isolated_between_tenants():
    a = org("Tenant A", "a@a.example")
    b = org("Tenant B", "b@b.example")
    secret = mk(b, "Secret Project", "confidential").json()
    assert a.get("/api/projects").json()["total"] == 0
    assert a.get("/api/projects?q=secret").json()["total"] == 0
    assert a.get(f"/api/projects/{secret['id']}").status_code == 404
    assert a.patch(f"/api/projects/{secret['id']}", json={"name": "pwned"}).status_code == 404
    assert a.delete(f"/api/projects/{secret['id']}").status_code == 404
    assert b.get(f"/api/projects/{secret['id']}").json()["name"] == "Secret Project"


def test_deleting_a_creator_keeps_the_project(db):
    owner = org()
    creator, cid = add_user(owner, "creator@acme.example")
    p = mk(creator, "Orphan").json()
    assert owner.delete(f"/api/users/{cid}").status_code == 204
    got = owner.get(f"/api/projects/{p['id']}").json()
    assert got["created_by"] is None and got["can_edit"] is True  # admin can still manage it
