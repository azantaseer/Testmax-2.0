"""runners, pairing requests, scans, job runner columns

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runners",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("info", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_runners_tenant_id", "runners", ["tenant_id"])

    op.create_table(
        "pairing_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_code_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("user_code", sa.Text(), nullable=False),
        sa.Column("requested_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("info", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE")),
        sa.Column("runner_id", sa.Uuid(), sa.ForeignKey("runners.id", ondelete="CASCADE")),
        sa.Column("pending_key", sa.Text()),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('pending','approved','consumed')", name="ck_pairing_status"),
    )
    op.create_index("ix_pairing_requests_user_code", "pairing_requests", ["user_code"])

    op.add_column("jobs", sa.Column("runner_id", sa.Uuid(), sa.ForeignKey("runners.id", ondelete="SET NULL")))
    op.add_column("jobs", sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "scans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), sa.ForeignKey("requirements.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("runner_id", sa.Uuid(), sa.ForeignKey("runners.id", ondelete="SET NULL")),
        sa.Column("application_url", sa.Text(), nullable=False),
        sa.Column("scan_policy", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("prompt_sha256", sa.Text()),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("requirement_snapshot", JSONB(), nullable=False, server_default="{}"),
        sa.Column("app_map", JSONB()),
        sa.Column("raw", sa.Text()),
        sa.Column("schema_issues", JSONB(), nullable=False, server_default="[]"),
        sa.Column("history", JSONB(), nullable=False, server_default="[]"),
        sa.Column("tcg_contract", JSONB()),
        sa.Column("tsg_contract", JSONB()),
        sa.Column("contract_issues", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status in ('COMPLETE','PARTIAL','FAILED')", name="ck_scans_status"),
    )
    op.create_index("ix_scans_tenant_id", "scans", ["tenant_id"])
    op.create_index("ix_scans_project_id", "scans", ["project_id"])
    op.create_index("ix_scans_requirement_id", "scans", ["requirement_id"])


def downgrade() -> None:
    op.drop_table("scans")
    op.drop_column("jobs", "cancel_requested")
    op.drop_column("jobs", "runner_id")
    op.drop_table("pairing_requests")
    op.drop_table("runners")
