"""Runners (customer machines that run browsers), their pairing requests, and scan results."""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Runner(Base):
    __tablename__ = "runners"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # sha256 of the key; the key itself is never stored
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    info: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # os, version
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PairingRequest(Base):
    __tablename__ = "pairing_requests"
    __table_args__ = (CheckConstraint("status in ('pending','approved','consumed')", name="ck_pairing_status"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_code_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    user_code: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    requested_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    info: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    runner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runners.id", ondelete="CASCADE"))
    pending_key: Mapped[str | None] = mapped_column(Text)  # handed to the runner once, then erased
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Scan(Base):
    __tablename__ = "scans"
    __table_args__ = (CheckConstraint("status in ('COMPLETE','PARTIAL','FAILED')", name="ck_scans_status"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requirements.id", ondelete="SET NULL"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    runner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runners.id", ondelete="SET NULL"))
    application_url: Mapped[str] = mapped_column(Text, nullable=False)
    scan_policy: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    stop_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    prompt_sha256: Mapped[str | None] = mapped_column(Text)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    requirement_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    app_map: Mapped[dict | None] = mapped_column(JSONB)
    raw: Mapped[str | None] = mapped_column(Text)
    schema_issues: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    tcg_contract: Mapped[dict | None] = mapped_column(JSONB)
    tsg_contract: Mapped[dict | None] = mapped_column(JSONB)
    contract_issues: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
