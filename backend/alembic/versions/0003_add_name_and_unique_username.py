"""Add separate name and unique public username.

Revision ID: 0003_name_username
Revises: 0002_split_auth_credentials
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_name_username"
down_revision = "0002_split_auth_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("users")}

    if "name" not in columns:
        op.add_column("users", sa.Column("name", sa.String(length=120), nullable=True))

    # The old user_name column was used like a full name. Preserve it in name,
    # then assign deterministic unique handles for existing users.
    op.execute("UPDATE users SET name = user_name WHERE name IS NULL")
    op.execute("UPDATE users SET user_name = 'user_' || id")

    op.alter_column("users", "name", existing_type=sa.String(length=120), nullable=False)
    op.alter_column(
        "users",
        "user_name",
        existing_type=sa.String(length=120),
        type_=sa.String(length=50),
        existing_nullable=False,
    )

    inspector = sa.inspect(bind)
    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("users")
    }
    if "uq_users_user_name" not in unique_constraints:
        op.create_unique_constraint("uq_users_user_name", "users", ["user_name"])

    indexes = {index["name"] for index in inspector.get_indexes("users")}
    if "ix_users_user_name" not in indexes:
        op.create_index("ix_users_user_name", "users", ["user_name"])


def downgrade() -> None:
    op.drop_index("ix_users_user_name", table_name="users")
    op.drop_constraint("uq_users_user_name", "users", type_="unique")
    op.alter_column(
        "users",
        "user_name",
        existing_type=sa.String(length=50),
        type_=sa.String(length=120),
        existing_nullable=False,
    )
    op.drop_column("users", "name")
