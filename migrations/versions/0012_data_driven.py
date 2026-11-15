"""data-driven testing: data variations on test cases, the keys a script reads, the variation a run used

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("data_sets", JSONB(), nullable=False, server_default="[]"))
    op.add_column("test_scripts", sa.Column("data_keys", JSONB(), nullable=False, server_default="[]"))
    op.add_column("test_scripts", sa.Column("data_gaps", JSONB(), nullable=False, server_default="[]"))
    op.add_column("test_runs", sa.Column("data_set", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("test_runs", "data_set")
    op.drop_column("test_scripts", "data_gaps")
    op.drop_column("test_scripts", "data_keys")
    op.drop_column("test_cases", "data_sets")
