"""Notes on paper orders.

Revision ID: 0017_notes_field
Revises: 0016_slippage_charges
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_notes_field"
down_revision = "0016_slippage_charges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_orders", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("paper_orders", "notes")
