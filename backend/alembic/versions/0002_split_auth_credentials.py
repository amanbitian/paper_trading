"""Split auth credentials and sessions from users.

Revision ID: 0002_split_auth_credentials
Revises: 0001_initial_schema
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_split_auth_credentials"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}
    return index_name in indexes


def _unique_constraint_exists(table_name: str, constraint_name: str) -> bool:
    constraints = {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table_name)
    }
    return constraint_name in constraints


def upgrade() -> None:
    if not _table_exists("user_credentials"):
        op.create_table(
            "user_credentials",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("password_updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", name="uq_user_credentials_user_id"),
        )
    elif not _unique_constraint_exists("user_credentials", "uq_user_credentials_user_id"):
        op.create_unique_constraint("uq_user_credentials_user_id", "user_credentials", ["user_id"])
    if not _index_exists("user_credentials", "ix_user_credentials_user_id"):
        op.create_index("ix_user_credentials_user_id", "user_credentials", ["user_id"])

    if not _table_exists("auth_sessions"):
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token_jti_hash", sa.String(length=64), nullable=False),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("ip_address", sa.String(length=80), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("token_jti_hash", name="uq_auth_sessions_token_jti_hash"),
        )
    elif not _unique_constraint_exists("auth_sessions", "uq_auth_sessions_token_jti_hash"):
        op.create_unique_constraint("uq_auth_sessions_token_jti_hash", "auth_sessions", ["token_jti_hash"])
    if not _index_exists("auth_sessions", "ix_auth_sessions_user_id"):
        op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    if not _index_exists("auth_sessions", "ix_auth_sessions_token_jti_hash"):
        op.create_index("ix_auth_sessions_token_jti_hash", "auth_sessions", ["token_jti_hash"])
    if not _index_exists("auth_sessions", "ix_auth_sessions_expires_at"):
        op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    if not _table_exists("password_reset_tokens"):
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
        )
    elif not _unique_constraint_exists("password_reset_tokens", "uq_password_reset_tokens_token_hash"):
        op.create_unique_constraint("uq_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"])
    if not _index_exists("password_reset_tokens", "ix_password_reset_tokens_user_id"):
        op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    if not _index_exists("password_reset_tokens", "ix_password_reset_tokens_token_hash"):
        op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"])

    user_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "password_hash" in user_columns:
        op.execute(
            """
            INSERT INTO user_credentials (user_id, password_hash, password_updated_at, created_at, updated_at)
            SELECT id, password_hash, now(), now(), now()
            FROM users
            WHERE password_hash IS NOT NULL
            ON CONFLICT (user_id) DO NOTHING
            """
        )
        op.drop_column("users", "password_hash")


def downgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE users
        SET password_hash = user_credentials.password_hash
        FROM user_credentials
        WHERE users.id = user_credentials.user_id
        """
    )
    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_token_jti_hash", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_user_credentials_user_id", table_name="user_credentials")
    op.drop_table("user_credentials")
