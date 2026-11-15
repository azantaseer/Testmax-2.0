"""test runs and their screenshots

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("script_id", sa.Uuid(), sa.ForeignKey("test_scripts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_case_id", sa.Uuid(), sa.ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("runner_id", sa.Uuid(), sa.ForeignKey("runners.id", ondelete="SET NULL")),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("headed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("steps", JSONB(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("log", sa.Text(), nullable=False, server_default=""),
        sa.Column("script_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "seq", name="uq_test_runs_project_seq"),
    )
    op.create_index("ix_test_runs_tenant_id", "test_runs", ["tenant_id"])
    op.create_index("ix_test_runs_project_id", "test_runs", ["project_id"])
    op.create_index("ix_test_runs_script_id", "test_runs", ["script_id"])
    op.create_table(
        "test_run_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_test_run_artifacts_tenant_id", "test_run_artifacts", ["tenant_id"])
    op.create_index("ix_test_run_artifacts_run_id", "test_run_artifacts", ["run_id"])


def downgrade() -> None:
    op.drop_table("test_run_artifacts")
    op.drop_table("test_runs")
