"""choose a runner per job; per-organization limit of jobs running at the same time

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("target_runner_id", sa.Uuid(), sa.ForeignKey("runners.id", ondelete="SET NULL")))
    op.create_index("ix_jobs_target_runner", "jobs", ["target_runner_id"], postgresql_where=sa.text("target_runner_id is not null"))
    op.add_column("tenants", sa.Column("job_concurrency", sa.Integer()))  # null = the system default
    op.create_check_constraint("ck_tenants_job_concurrency", "tenants", "job_concurrency is null or (job_concurrency >= 1 and job_concurrency <= 100)")


def downgrade() -> None:
    op.drop_constraint("ck_tenants_job_concurrency", "tenants", type_="check")
    op.drop_column("tenants", "job_concurrency")
    op.drop_index("ix_jobs_target_runner", table_name="jobs")
    op.drop_column("jobs", "target_runner_id")
