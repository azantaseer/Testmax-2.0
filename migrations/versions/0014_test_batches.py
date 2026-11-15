"""test batches: scripts run in order in one browser session

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("on_failure", sa.Text(), nullable=False, server_default="stop"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "seq", name="uq_test_batches_project_seq"),
    )
    op.create_index("ix_test_batches_tenant_id", "test_batches", ["tenant_id"])
    op.create_index("ix_test_batches_project_id", "test_batches", ["project_id"])
    op.create_table(
        "test_batch_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("test_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("script_id", sa.Uuid(), sa.ForeignKey("test_scripts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("data_set_id", sa.Text(), nullable=False, server_default="default"),
    )
    op.create_index("ix_test_batch_items_tenant_id", "test_batch_items", ["tenant_id"])
    op.create_index("ix_test_batch_items_batch_id", "test_batch_items", ["batch_id"])
    op.create_index("ix_test_batch_items_script_id", "test_batch_items", ["script_id"])
    op.create_table(
        "test_batch_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("test_batches.id", ondelete="CASCADE")),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("runner_id", sa.Uuid(), sa.ForeignKey("runners.id", ondelete="SET NULL")),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("batch_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("batch_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("on_failure", sa.Text(), nullable=False, server_default="stop"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("headed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("items", JSONB(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("log", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "seq", name="uq_test_batch_runs_project_seq"),
    )
    op.create_index("ix_test_batch_runs_tenant_id", "test_batch_runs", ["tenant_id"])
    op.create_index("ix_test_batch_runs_project_id", "test_batch_runs", ["project_id"])
    op.create_index("ix_test_batch_runs_batch_id", "test_batch_runs", ["batch_id"])
    op.add_column("test_runs", sa.Column("batch_run_id", sa.Uuid(), sa.ForeignKey("test_batch_runs.id", ondelete="SET NULL")))
    op.add_column("test_runs", sa.Column("batch_position", sa.Integer()))
    op.create_index("ix_test_runs_batch_run_id", "test_runs", ["batch_run_id"])


def downgrade() -> None:
    op.drop_index("ix_test_runs_batch_run_id", table_name="test_runs")
    op.drop_column("test_runs", "batch_position")
    op.drop_column("test_runs", "batch_run_id")
    op.drop_table("test_batch_runs")
    op.drop_table("test_batch_items")
    op.drop_table("test_batches")
