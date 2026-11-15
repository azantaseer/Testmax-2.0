"""test case sets and test cases

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_case_sets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scan_id", sa.Uuid(), sa.ForeignKey("scans.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("provider", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("prompt_sha256", sa.Text()),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("coverage", JSONB(), nullable=False, server_default="{}"),
        sa.Column("warnings", JSONB(), nullable=False, server_default="[]"),
        sa.Column("mapping_gaps", JSONB(), nullable=False, server_default="[]"),
        sa.Column("not_generated", JSONB(), nullable=False, server_default="[]"),
        sa.Column("raw", sa.Text()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for col in ("tenant_id", "project_id", "requirement_id"):
        op.create_index(f"ix_test_case_sets_{col}", "test_case_sets", [col])
    op.create_table(
        "test_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("set_id", sa.Uuid(), sa.ForeignKey("test_case_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("case_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("overview", sa.Text(), nullable=False, server_default=""),
        sa.Column("prerequisites", JSONB(), nullable=False, server_default="[]"),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("expected_result", sa.Text(), nullable=False),
        sa.Column("steps", JSONB(), nullable=False, server_default="[]"),
        sa.Column("set_position", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "seq", name="uq_test_cases_project_seq"),
    )
    for col in ("tenant_id", "project_id", "requirement_id", "set_id"):
        op.create_index(f"ix_test_cases_{col}", "test_cases", [col])


def downgrade() -> None:
    op.drop_table("test_cases")
    op.drop_table("test_case_sets")
