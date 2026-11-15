import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from docx import Document as DocxDocument
from sqlalchemy import select

from app import jobs, llm
from app.engines import requirements as req_engine
from app.models_work import Document, Job, Requirement
from app.worker import new_session, run_job
from tests.conftest import GOOD_PASSWORD, login, new_client, signup_org

PROMPT_SHA256 = "400b0fcc549326c44fa947fe4eaff4dc0b0666d2674c89769020cfcf2d481e30"
PILOT1_PROMPT = Path(r"D:\Projects\TestMax-Independent-Pilot\system-prompts\TestMax_Requirements_Extraction_System_Prompt_v1.2.txt")

BRD = b"The user can log in with a username and password. Locked users see an error message."
REPLY = {
    "requirements": [
        {"summary": "Login with valid credentials", "description": "Overview:\nUser logs in.", "acceptanceCriteria": "Given valid data, user lands on the inventory page."},
        {"summary": "Locked user is blocked", "description": "Overview:\nLocked users cannot log in.", "acceptanceCriteria": "An error message is shown."},
    ]
}


def org(name="Acme Corp", email="owner@acme.example"):
    c = new_client()
    assert signup_org(c, org=name, email=email).status_code == 201
    return c


def project(c, name="Web App"):
    return c.post("/api/projects", json={"name": name}).json()["id"]


def upload(c, pid, data=BRD, filename="brd.txt"):
    return c.post(f"/api/projects/{pid}/requirements/generate", files={"file": (filename, data, "application/octet-stream")})


def fake_llm(monkeypatch, reply=REPLY, raw=None, error=None, calls=None):
    def complete(provider, model, system, user, client=None):
        if calls is not None:
            calls.append({"provider": provider, "model": model, "system": system, "user": user})
        if error:
            raise error
        return llm.LLMResult(text=raw if raw is not None else json.dumps(reply), tokens_in=111, tokens_out=222)

    monkeypatch.setattr(llm, "complete", complete)


def run_next(worker="t-worker"):
    with new_session() as s:
        jid = jobs.claim(s, worker, ["generate_requirements"])
    assert jid is not None
    return jid, run_job(jid, worker)


def job_row(jid):
    with new_session() as s:
        return s.get(Job, jid)


# ---- the prompt must stay unchanged ----------------------------------------
def test_requirements_prompt_is_unchanged_from_pilot1():
    text, sha = req_engine.load_system_prompt()
    assert sha == PROMPT_SHA256
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == sha
    if PILOT1_PROMPT.exists():
        assert hashlib.sha256(PILOT1_PROMPT.read_bytes()).hexdigest() == sha


# ---- upload validation ------------------------------------------------------
def test_upload_validation():
    c = org()
    pid = project(c)
    assert upload(c, pid, b"x", "brd.exe").status_code == 422
    assert upload(c, pid, b"", "brd.txt").status_code == 422
    assert upload(c, pid, b"\xff\xfe\x00bad", "brd.txt").status_code == 422
    assert upload(c, pid, b"   \n  ", "brd.txt").status_code == 422
    assert upload(c, pid, b"not a pdf", "brd.pdf").status_code == 422
    big = upload(c, pid, b"a" * (10 * 1024 * 1024 + 1), "big.txt")
    assert big.status_code == 413 and big.json()["error"]["code"] == "file_too_large"
    assert c.get(f"/api/projects/{pid}/jobs").json()["jobs"] == []  # nothing queued for any rejected file


def test_upload_requires_login_and_existing_project():
    c = org()
    pid = project(c)
    assert upload(new_client(), pid).status_code == 401
    assert upload(c, "00000000-0000-0000-0000-000000000000").status_code == 404


def test_upload_without_provider_key_is_rejected(monkeypatch):
    c = org()
    pid = project(c)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    r = upload(c, pid)
    assert r.status_code == 400 and r.json()["error"]["code"] == "provider_not_configured"


def test_docx_upload_is_extracted_including_tables():
    doc = DocxDocument()
    doc.add_paragraph("Users can reset their password.")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Field"
    table.rows[0].cells[1].text = "Email"
    buf = io.BytesIO()
    doc.save(buf)
    text = req_engine.extract_text("brd.docx", buf.getvalue())
    assert "reset their password" in text and "Field\tEmail" in text


def test_markdown_csv_and_tsv_uploads_are_read_as_text():
    md = "# Login\n\n- Users can log in.\n".encode()
    csv = "ID,Requirement\r\nR1,Users can log in\r\n".encode()
    tsv = "ID\tRequirement\nR1\tUsers can log in\n".encode()
    assert req_engine.extract_text("brd.md", md) == "# Login\n\n- Users can log in."
    assert req_engine.extract_text("brd.CSV", csv) == "ID,Requirement\r\nR1,Users can log in"  # the table stays as written
    assert req_engine.extract_text("brd.tsv", tsv) == "ID\tRequirement\nR1\tUsers can log in"
    # Excel writes a byte order mark at the start of a CSV; it must not reach the AI
    assert req_engine.extract_text("brd.csv", b"\xef\xbb\xbf" + csv).startswith("ID,Requirement")
    assert not req_engine.extract_text("brd.csv", b"\xef\xbb\xbf" + csv).startswith("﻿")
    # still checked: empty, not UTF-8, blank, and the old types are unchanged
    for name, data in (("a.md", b""), ("a.csv", b"\xff\xfe\x00bad"), ("a.tsv", b"  \n "), ("a.md", b"   ")):
        with pytest.raises(req_engine.DocumentError):
            req_engine.extract_text(name, data)
    with pytest.raises(req_engine.DocumentError, match="PDF, DOCX, TXT, MD, CSV or TSV"):
        req_engine.extract_text("brd.doc", b"x")  # .doc (old Word) needs another library and is not supported
    assert req_engine.extract_text("brd.txt", b"plain") == "plain"


def test_csv_upload_goes_through_to_the_model(monkeypatch, db):
    c = org()
    pid = project(c)
    calls = []
    fake_llm(monkeypatch, calls=calls)
    data = b"\xef\xbb\xbfID,Requirement\nR1,Users can log in\n"
    assert upload(c, pid, data, "reqs.csv").status_code == 202
    assert upload(c, pid, b"x", "reqs.doc").status_code == 422
    jid, state = run_next()
    assert state == "succeeded" and calls[0]["user"] == "ID,Requirement\nR1,Users can log in"


# ---- generate end to end (LLM mocked) ---------------------------------------
def test_generate_requirements_end_to_end(monkeypatch, db):
    c = org()
    pid = project(c)
    calls = []
    fake_llm(monkeypatch, calls=calls)
    r = upload(c, pid)
    assert r.status_code == 202
    job = r.json()
    assert job["status"] == "queued" and job["filename"] == "brd.txt" and job["provider"] == "openai"
    assert c.get(f"/api/jobs/{job['id']}").json()["progress"] == "Waiting in the queue"

    jid, state = run_next()
    assert state == "succeeded"
    done = c.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "succeeded" and done["result"]["requirement_count"] == 2 and done["result"]["prompt_sha256"] == PROMPT_SHA256
    # the unchanged system prompt and the document text went to the model
    assert calls[0]["system"] == req_engine.load_system_prompt()[0] and calls[0]["user"] == BRD.decode()
    # metering
    j = job_row(jid)
    assert (j.tokens_in, j.tokens_out, j.provider, j.model) == (111, 222, "openai", "system-default-model") and j.duration_ms >= 0

    listing = c.get(f"/api/projects/{pid}/requirements").json()
    assert listing["total"] == 2 and [r["key"] for r in listing["requirements"]] == ["REQ-001", "REQ-002"]
    one = c.get(f"/api/projects/{pid}/requirements/{listing['requirements'][0]['id']}").json()
    assert one["summary"] == "Login with valid credentials" and one["acceptance_criteria"].startswith("Given valid data")
    # the original file is kept in Postgres
    assert db.scalar(select(Document.data)) == BRD


def test_requirement_numbers_continue_across_generations(monkeypatch):
    c = org()
    pid = project(c)
    fake_llm(monkeypatch)
    upload(c, pid)
    run_next()
    upload(c, pid, b"A second document with more text.", "second.txt")
    run_next()
    keys = [r["key"] for r in c.get(f"/api/projects/{pid}/requirements").json()["requirements"]]
    assert keys == ["REQ-001", "REQ-002", "REQ-003", "REQ-004"]
    assert c.get(f"/api/projects/{pid}/requirements?q=locked").json()["total"] == 2


def test_code_fenced_reply_is_accepted(monkeypatch):
    c = org()
    pid = project(c)
    fake_llm(monkeypatch, raw="```json\n" + json.dumps(REPLY) + "\n```")
    upload(c, pid)
    assert run_next()[1] == "succeeded"


def test_invalid_reply_is_retried_then_fails_with_a_clear_message(monkeypatch):
    c = org()
    pid = project(c)
    fake_llm(monkeypatch, raw="Sorry, here are some thoughts but no JSON")
    upload(c, pid)
    jid, state = run_next()
    assert state == "requeued"
    assert "Retrying" in job_row(jid).progress
    assert run_next()[1] == "failed"
    j = job_row(jid)
    assert j.status == "failed" and "not valid JSON" in j.error and "2 attempts" in j.error
    assert c.get(f"/api/projects/{pid}/requirements").json()["total"] == 0  # nothing half-saved


def test_missing_field_in_reply_fails_validation(monkeypatch):
    c = org()
    pid = project(c)
    fake_llm(monkeypatch, reply={"requirements": [{"summary": "only a summary"}]})
    upload(c, pid)
    run_next()
    run_next()
    assert "missing a valid 'description'" in job_row(c.get(f"/api/projects/{pid}/jobs").json()["jobs"][0]["id"]).error


def test_empty_requirements_list_fails_without_retry(monkeypatch):
    c = org()
    pid = project(c)
    fake_llm(monkeypatch, reply={"requirements": []})
    upload(c, pid)
    jid, state = run_next()
    assert state == "failed" and job_row(jid).error == "No requirements were found in this document."


def test_provider_key_rejected_fails_immediately_and_retryable_errors_requeue(monkeypatch):
    c = org()
    pid = project(c)
    fake_llm(monkeypatch, error=llm.LLMError("OpenAI rejected the server's API key."))
    upload(c, pid)
    jid, state = run_next()
    assert state == "failed" and job_row(jid).error == "OpenAI rejected the server's API key." and job_row(jid).attempts == 1
    fake_llm(monkeypatch, error=llm.LLMError("OpenAI is rate limiting requests.", retryable=True))
    upload(c, pid, b"another document")
    jid2, state2 = run_next()
    assert state2 == "requeued" and job_row(jid2).status == "queued"


def test_unexpected_crash_fails_cleanly_without_leaking_details(monkeypatch):
    c = org()
    pid = project(c)

    def boom(*a, **k):
        raise RuntimeError("secret-internal-detail sk-12345")

    monkeypatch.setattr(llm, "complete", boom)
    upload(c, pid)
    jid, state = run_next()
    assert state == "failed"
    err = job_row(jid).error
    assert "unexpected error" in err and "sk-12345" not in err and "secret-internal" not in err


def test_deleted_document_or_project_fails_cleanly(monkeypatch, db):
    c = org()
    pid = project(c)
    fake_llm(monkeypatch)
    upload(c, pid)
    db.execute(Document.__table__.delete())
    db.commit()
    jid, state = run_next()
    assert state == "failed" and "no longer available" in job_row(jid).error


# ---- which model is used ----------------------------------------------------
def test_user_override_decides_provider_and_model_and_is_fixed_at_upload(monkeypatch):
    owner = org()
    owner.put("/api/tenant/ai-settings", json={"provider": "openai", "model": "tenant-model"})
    owner.post("/api/users", json={"name": "Una", "email": "una@acme.example", "password": GOOD_PASSWORD})
    una = new_client()
    login(una, "una@acme.example")
    pid = project(owner)
    calls = []
    fake_llm(monkeypatch, calls=calls)

    upload(owner, pid)  # owner: organization default
    una.put("/api/me/ai-settings", json={"provider": "anthropic", "model": "claude-una"})
    queued = upload(una, pid, b"Una's document")  # user setting beats organization setting
    assert queued.json()["provider"] == "anthropic" and queued.json()["model"] == "claude-una"
    una.put("/api/me/ai-settings", json={"provider": "openai", "model": "changed-later"})  # too late for the queued job
    run_next()
    run_next()
    assert {(c["provider"], c["model"]) for c in calls} == {("openai", "tenant-model"), ("anthropic", "claude-una")}
    models = {r["model"] for r in owner.get(f"/api/projects/{pid}/requirements").json()["requirements"]}
    assert models == {"tenant-model", "claude-una"}


# ---- isolation --------------------------------------------------------------
def test_requirements_and_jobs_are_isolated_between_tenants(monkeypatch):
    a = org("Tenant A", "a@a.example")
    b = org("Tenant B", "b@b.example")
    pid_b = project(b, "Secret")
    fake_llm(monkeypatch)
    queued = upload(b, pid_b).json()
    run_next()
    req_id = b.get(f"/api/projects/{pid_b}/requirements").json()["requirements"][0]["id"]
    assert a.get(f"/api/jobs/{queued['id']}").status_code == 404
    assert a.get(f"/api/projects/{pid_b}/requirements").status_code == 404
    assert a.get(f"/api/projects/{pid_b}/requirements/{req_id}").status_code == 404
    assert a.get(f"/api/projects/{pid_b}/jobs").status_code == 404
    assert upload(a, pid_b).status_code == 404
    pid_a = project(a, "Mine")
    assert a.get(f"/api/projects/{pid_a}/requirements/{req_id}").status_code == 404  # right id, wrong project/tenant


def test_deleting_a_project_removes_its_requirements_and_jobs(monkeypatch, db):
    c = org()
    pid = project(c)
    fake_llm(monkeypatch)
    upload(c, pid)
    run_next()
    assert c.delete(f"/api/projects/{pid}").status_code == 204
    assert db.scalar(select(Requirement.id)) is None and db.scalar(select(Job.id)) is None and db.scalar(select(Document.id)) is None


# ---- provider adapter -------------------------------------------------------
class FakeOpenAI:
    def __init__(self, content="hello", usage=(5, 7)):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.content, self.usage, self.kwargs = content, usage, None

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=self.usage[0], completion_tokens=self.usage[1]),
        )


class FakeAnthropic:
    def __init__(self):
        self.messages = SimpleNamespace(stream=self._stream)
        self.kwargs = None

    def _stream(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="part one "), SimpleNamespace(type="thinking", text="ignored"), SimpleNamespace(type="text", text="part two")],
            usage=SimpleNamespace(input_tokens=11, output_tokens=22),
        )

        class Ctx:
            def __enter__(self_):
                return SimpleNamespace(get_final_message=lambda: message)

            def __exit__(self_, *a):
                return False

        return Ctx()


def test_openai_call_shape_matches_pilot1():
    fake = FakeOpenAI()
    out = llm.complete("openai", "gpt-x", "SYSTEM", "USER", client=fake)
    assert (out.text, out.tokens_in, out.tokens_out) == ("hello", 5, 7)
    assert fake.kwargs == {"model": "gpt-x", "messages": [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER"}]}


def test_anthropic_call_uses_streaming_and_joins_text_blocks():
    fake = FakeAnthropic()
    out = llm.complete("anthropic", "claude-x", "SYSTEM", "USER", client=fake)
    assert (out.text, out.tokens_in, out.tokens_out) == ("part one part two", 11, 22)
    assert fake.kwargs["system"] == "SYSTEM" and fake.kwargs["messages"] == [{"role": "user", "content": "USER"}] and fake.kwargs["max_tokens"] > 1000


@pytest.mark.parametrize("sdk,provider", [(openai, "openai"), (anthropic, "anthropic")])
def test_provider_errors_are_classified(sdk, provider):
    req = httpx.Request("POST", "https://api.example/v1")

    def status_error(cls, code):
        return cls("secret-text sk-LEAK", response=httpx.Response(code, request=req), body=None)

    cases = [
        (status_error(sdk.AuthenticationError, 401), False, "rejected"),
        (status_error(sdk.NotFoundError, 404), False, "does not know"),
        (status_error(sdk.RateLimitError, 429), True, "rate limiting"),
        (status_error(sdk.InternalServerError, 500), True, "temporary"),
        (sdk.APIConnectionError(request=req), True, "Could not reach"),
    ]
    for exc, retryable, fragment in cases:
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(exc))),
            messages=SimpleNamespace(stream=lambda **k: (_ for _ in ()).throw(exc)),
        )
        with pytest.raises(llm.LLMError) as info:
            llm.complete(provider, "m", "s", "u", client=client)
        assert info.value.retryable is retryable and fragment in info.value.message
        assert "LEAK" not in info.value.message and "secret-text" not in info.value.message


def test_make_client_requires_a_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(llm.LLMError, match="No API key"):
        llm.make_client("anthropic")


# ---- a job whose worker disappeared is reported honestly --------------------
def test_job_with_expired_lease_is_reported_as_stalled_until_recovered(monkeypatch):
    from sqlalchemy import text as sql

    c = org()
    pid = project(c)
    fake_llm(monkeypatch)
    queued = upload(c, pid).json()
    with new_session() as s:
        jid = jobs.claim(s, "dead-worker", ["generate_requirements"])
        s.execute(sql("update jobs set lease_expires_at = now() - interval '1 second' where id=:i"), {"i": jid})
        s.commit()
    j = c.get(f"/api/jobs/{queued['id']}").json()
    assert j["status"] == "running" and j["stalled"] is True and "stopped responding" in j["progress"]
    with new_session() as s:
        jobs.reap_expired(s)  # a live worker's reaper picks it up
    assert c.get(f"/api/jobs/{queued['id']}").json()["stalled"] is False
    assert run_next("new-worker")[1] == "succeeded"
    assert c.get(f"/api/jobs/{queued['id']}").json()["attempts"] == 2
    assert c.get(f"/api/projects/{pid}/requirements").json()["total"] == 2  # once, not twice
