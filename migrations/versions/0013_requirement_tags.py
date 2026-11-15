"""requirement tags (free-form labels used to group requirements)

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("requirements", sa.Column("tags", JSONB(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("requirements", "tags")
