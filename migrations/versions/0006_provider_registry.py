"""providers are a registry, not a database constraint; scans keep notes

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_tenants_ai_provider", "tenants", type_="check")
    op.drop_constraint("ck_users_ai_provider", "users", type_="check")
    op.add_column("scans", sa.Column("notes", JSONB(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("scans", "notes")
    op.create_check_constraint("ck_users_ai_provider", "users", "ai_provider in ('openai','anthropic')")
    op.create_check_constraint("ck_tenants_ai_provider", "tenants", "ai_provider in ('openai','anthropic')")
