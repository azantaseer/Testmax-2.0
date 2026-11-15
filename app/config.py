"""Settings from environment variables (and a local .env when running outside Docker)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # real environment variables win over .env


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    database_url: str
    openai_api_key: str
    anthropic_api_key: str
    default_ai_provider: str
    default_ai_model: str
    cookie_secure: bool
    session_hours: int
    remember_days: int
    password_min_length: int
    job_tenant_concurrency: int
    job_tenant_concurrency_max: int
    job_lease_seconds: int
    job_heartbeat_seconds: int
    job_max_attempts: int
    worker_concurrency: int
    anthropic_max_output_tokens: int
    llm_timeout_seconds: int
    job_max_tokens: int


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        default_ai_provider=os.environ.get("DEFAULT_AI_PROVIDER", "").strip(),
        default_ai_model=os.environ.get("DEFAULT_AI_MODEL", "").strip(),
        cookie_secure=_bool("COOKIE_SECURE", False),
        session_hours=int(os.environ.get("SESSION_HOURS", "12")),
        remember_days=int(os.environ.get("REMEMBER_DAYS", "30")),
        password_min_length=int(os.environ.get("PASSWORD_MIN_LENGTH", "10")),
        job_tenant_concurrency=int(os.environ.get("JOB_TENANT_CONCURRENCY", "3")),
        job_tenant_concurrency_max=int(os.environ.get("JOB_TENANT_CONCURRENCY_MAX", "20")),
        job_lease_seconds=int(os.environ.get("JOB_LEASE_SECONDS", "45")),
        job_heartbeat_seconds=int(os.environ.get("JOB_HEARTBEAT_SECONDS", "10")),
        job_max_attempts=int(os.environ.get("JOB_MAX_ATTEMPTS", "2")),
        worker_concurrency=int(os.environ.get("WORKER_CONCURRENCY", "2")),
        anthropic_max_output_tokens=int(os.environ.get("ANTHROPIC_MAX_OUTPUT_TOKENS", "32000")),
        llm_timeout_seconds=int(os.environ.get("LLM_TIMEOUT_SECONDS", "900")),
        job_max_tokens=int(os.environ.get("JOB_MAX_TOKENS", "4000000")),
    )
