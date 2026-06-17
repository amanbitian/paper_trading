"""Add stock news ingestion tables.

Revision ID: 0024_stock_news
Revises: 0023_stock_fundamentals_latest
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0024_stock_news"
down_revision = "0023_stock_fundamentals_latest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_news_articles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("provider_article_id", sa.String(length=160), nullable=True),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("source_domain", sa.String(length=160), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sentiment_score", sa.Float(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url_hash", name="uq_stock_news_articles_url_hash"),
    )
    op.create_index("ix_stock_news_articles_id", "stock_news_articles", ["id"])
    op.create_index("ix_stock_news_articles_url_hash", "stock_news_articles", ["url_hash"])
    op.create_index("ix_stock_news_articles_content_hash", "stock_news_articles", ["content_hash"])
    op.create_index("ix_stock_news_articles_provider_article_id", "stock_news_articles", ["provider_article_id"])
    op.create_index("ix_stock_news_articles_source", "stock_news_articles", ["source"])
    op.create_index("ix_stock_news_articles_source_domain", "stock_news_articles", ["source_domain"])
    op.create_index("ix_stock_news_articles_provider", "stock_news_articles", ["provider"])
    op.create_index("ix_stock_news_articles_published_at", "stock_news_articles", ["published_at"])
    op.create_index(
        "ix_stock_news_articles_unscored",
        "stock_news_articles",
        ["id"],
        postgresql_where=sa.text("sentiment_score IS NULL"),
    )

    op.create_table(
        "stock_news_links",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=False),
        sa.Column("match_method", sa.String(length=30), nullable=True),
        sa.Column("match_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["stock_news_articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("article_id", "stock_id", name="uq_stock_news_links_article_stock"),
    )
    op.create_index("ix_stock_news_links_id", "stock_news_links", ["id"])
    op.create_index("ix_stock_news_links_article_id", "stock_news_links", ["article_id"])
    op.create_index("ix_stock_news_links_stock_id", "stock_news_links", ["stock_id"])
    op.create_index("ix_stock_news_links_published_at", "stock_news_links", ["published_at"])
    op.create_index("ix_stock_news_links_stock_published", "stock_news_links", ["stock_id", "published_at"])

    op.create_table(
        "company_aliases",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("alias_type", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias", "stock_id", name="uq_company_aliases_alias_stock"),
    )
    op.create_index("ix_company_aliases_id", "company_aliases", ["id"])
    op.create_index("ix_company_aliases_stock_id", "company_aliases", ["stock_id"])
    op.create_index("ix_company_aliases_normalized_alias", "company_aliases", ["normalized_alias"])

    op.create_table(
        "stock_news_ingestion_meta",
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.SmallInteger(), nullable=False),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_article_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_provider", sa.String(length=50), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("tier BETWEEN 1 AND 3", name="ck_stock_news_ingestion_meta_tier"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("stock_id"),
    )
    op.create_index("ix_stock_news_ingestion_meta_tier_ingested", "stock_news_ingestion_meta", ["tier", "last_ingested_at"])

    op.create_table(
        "news_provider_quota_state",
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("used_today", sa.Integer(), nullable=False),
        sa.Column("limit_daily", sa.Integer(), nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_cursor", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("provider"),
    )

    op.create_table(
        "news_ingestion_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("articles_in", sa.Integer(), nullable=False),
        sa.Column("articles_new", sa.Integer(), nullable=False),
        sa.Column("links_new", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_news_ingestion_runs_id", "news_ingestion_runs", ["id"])
    op.create_index("ix_news_ingestion_runs_provider", "news_ingestion_runs", ["provider"])
    op.create_index("ix_news_ingestion_runs_mode", "news_ingestion_runs", ["mode"])
    op.create_index("ix_news_ingestion_runs_stock_id", "news_ingestion_runs", ["stock_id"])
    op.create_index("ix_news_ingestion_runs_started_at", "news_ingestion_runs", ["started_at"])
    op.create_index("ix_news_ingestion_runs_status", "news_ingestion_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_news_ingestion_runs_status", table_name="news_ingestion_runs")
    op.drop_index("ix_news_ingestion_runs_started_at", table_name="news_ingestion_runs")
    op.drop_index("ix_news_ingestion_runs_stock_id", table_name="news_ingestion_runs")
    op.drop_index("ix_news_ingestion_runs_mode", table_name="news_ingestion_runs")
    op.drop_index("ix_news_ingestion_runs_provider", table_name="news_ingestion_runs")
    op.drop_index("ix_news_ingestion_runs_id", table_name="news_ingestion_runs")
    op.drop_table("news_ingestion_runs")

    op.drop_table("news_provider_quota_state")

    op.drop_index("ix_stock_news_ingestion_meta_tier_ingested", table_name="stock_news_ingestion_meta")
    op.drop_table("stock_news_ingestion_meta")

    op.drop_index("ix_company_aliases_normalized_alias", table_name="company_aliases")
    op.drop_index("ix_company_aliases_stock_id", table_name="company_aliases")
    op.drop_index("ix_company_aliases_id", table_name="company_aliases")
    op.drop_table("company_aliases")

    op.drop_index("ix_stock_news_links_stock_published", table_name="stock_news_links")
    op.drop_index("ix_stock_news_links_published_at", table_name="stock_news_links")
    op.drop_index("ix_stock_news_links_stock_id", table_name="stock_news_links")
    op.drop_index("ix_stock_news_links_article_id", table_name="stock_news_links")
    op.drop_index("ix_stock_news_links_id", table_name="stock_news_links")
    op.drop_table("stock_news_links")

    op.drop_index("ix_stock_news_articles_unscored", table_name="stock_news_articles")
    op.drop_index("ix_stock_news_articles_published_at", table_name="stock_news_articles")
    op.drop_index("ix_stock_news_articles_provider", table_name="stock_news_articles")
    op.drop_index("ix_stock_news_articles_source_domain", table_name="stock_news_articles")
    op.drop_index("ix_stock_news_articles_source", table_name="stock_news_articles")
    op.drop_index("ix_stock_news_articles_provider_article_id", table_name="stock_news_articles")
    op.drop_index("ix_stock_news_articles_content_hash", table_name="stock_news_articles")
    op.drop_index("ix_stock_news_articles_url_hash", table_name="stock_news_articles")
    op.drop_index("ix_stock_news_articles_id", table_name="stock_news_articles")
    op.drop_table("stock_news_articles")
