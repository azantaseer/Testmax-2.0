"""Test setup: a separate `testmax_test` database on the compose Postgres (127.0.0.1:5432).

Fake provider keys are set before the app is imported, so no real key is ever used or needed.
"""
import os
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
_pw = os.environ.get("POSTGRES_PASSWORD") or dotenv_values(ROOT / ".env").get("POSTGRES_PASSWORD")
if not _pw:
    raise RuntimeError("POSTGRES_PASSWORD not found (set it in .env) and `docker compose up -d db` must be running")

TEST_DB = "testmax_test"
os.environ["DATABASE_URL"] = f"postgresql+psycopg://testmax:{_pw}@127.0.0.1:5432/{TEST_DB}"
os.environ["OPENAI_API_KEY"] = "test-openai-key-not-real"
os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key-not-real"
os.environ["DEFAULT_AI_PROVIDER"] = "openai"
os.environ["DEFAULT_AI_MODEL"] = "system-default-model"
os.environ["PASSWORD_MIN_LENGTH"] = "10"

from app.db import get_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Tenant, User  # noqa: E402
from app.security import hash_password, login_throttle  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

GOOD_PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session", autouse=True)
def _database():
    admin = psycopg.connect(f"host=127.0.0.1 user=testmax password={_pw} dbname=testmax", autocommit=True)
    exists = admin.execute("select 1 from pg_database where datname=%s", (TEST_DB,)).fetchone()
    if not exists:
        admin.execute(f'create database "{TEST_DB}"')
    admin.close()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("drop schema public cascade"))
        conn.execute(text("create schema public"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _no_real_tool_probe(monkeypatch):
    """Tests never call a real provider; the tool-calling probe is stubbed to 'unknown' unless a test sets it."""
    from app import ai_providers

    monkeypatch.setattr(ai_providers, "check_tool_calling", lambda provider, model: (None, ""))
    yield


@pytest.fixture(autouse=True)
def _clean():
    with get_engine().begin() as conn:
        conn.execute(text("truncate tenants cascade"))
    login_throttle._failures.clear()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def new_client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def db():
    with Session(get_engine()) as s:
        yield s


def signup_org(c: TestClient, org="Acme Corp", email="owner@acme.example", name="Olive Owner", **extra):
    body = {
        "organization_name": org,
        "name": name,
        "email": email,
        "password": GOOD_PASSWORD,
        **extra,
    }
    return c.post("/api/auth/signup-organization", json=body)


def make_user(db: Session, tenant_id, email, role="user", status="active", password=GOOD_PASSWORD) -> User:
    user = User(
        tenant_id=tenant_id,
        name=email.split("@")[0],
        email=email,
        password_hash=hash_password(password),
        role=role,
        status=status,
    )
    db.add(user)
    db.commit()
    return user


def login(c: TestClient, email, password=GOOD_PASSWORD, **extra):
    return c.post("/api/auth/login", json={"email": email, "password": password, **extra})


__all__ = ["Tenant"]
