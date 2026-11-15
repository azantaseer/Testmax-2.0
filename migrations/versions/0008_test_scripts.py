"""test scripts

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_scripts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_case_id", sa.Uuid(), sa.ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False, server_default="javascript"),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("provider", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("prompt_sha256", sa.Text()),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("elements_used", JSONB(), nullable=False, server_default="[]"),
        sa.Column("steps_without_locator", JSONB(), nullable=False, server_default="[]"),
        sa.Column("not_automated", JSONB(), nullable=False, server_default="[]"),
        sa.Column("blocked_message", sa.Text()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "seq", name="uq_test_scripts_project_seq"),
        sa.UniqueConstraint("test_case_id", name="uq_test_scripts_case"),
    )
    op.create_index("ix_test_scripts_tenant_id", "test_scripts", ["tenant_id"])
    op.create_index("ix_test_scripts_project_id", "test_scripts", ["project_id"])


def downgrade() -> None:
    op.drop_table("test_scripts")
