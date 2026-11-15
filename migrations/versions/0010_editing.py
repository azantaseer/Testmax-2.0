"""editing: version, updated_at and edited flag on requirements and test cases; the test case version a script was written from

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("requirements", "test_cases"):
        op.add_column(table, sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        op.add_column(table, sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        op.add_column(table, sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")))
        op.add_column(table, sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("test_scripts", sa.Column("case_version", sa.Integer(), nullable=False, server_default="1"))
    # existing rows: "updated" means "created" until somebody edits them
    op.execute("UPDATE requirements SET updated_at = created_at")
    op.execute("UPDATE test_cases SET updated_at = created_at")


def downgrade() -> None:
    op.drop_column("test_scripts", "case_version")
    for table in ("test_cases", "requirements"):
        for column in ("edited", "updated_by", "updated_at", "version"):
            op.drop_column(table, column)
