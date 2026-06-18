"""Email alert preference on users.

Revision ID: 0018_email_alerts
Revises: 0017_notes_field
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_email_alerts"
down_revision = "0017_notes_field"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_alerts_enabled", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "email_alerts_enabled")
