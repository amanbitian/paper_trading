from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class StockNewsArticle(Base):
    __tablename__ = "stock_news_articles"
    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_stock_news_articles_url_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    provider_article_id: Mapped[str | None] = mapped_column(String(160), index=True)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(120), index=True)
    source_domain: Mapped[str | None] = mapped_column(String(160), index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    published_at = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    body: Mapped[str | None] = mapped_column(Text)
    raw_payload = mapped_column(JSONB)
    ingested_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    links = relationship("StockNewsLink", back_populates="article", cascade="all, delete-orphan")


class StockNewsLink(Base):
    __tablename__ = "stock_news_links"
    __table_args__ = (
        UniqueConstraint("article_id", "stock_id", name="uq_stock_news_links_article_stock"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("stock_news_articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    published_at = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    match_method: Mapped[str | None] = mapped_column(String(30))
    match_evidence = mapped_column(JSONB)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    article = relationship("StockNewsArticle", back_populates="links")
    stock = relationship("Stock")


class CompanyAlias(Base):
    __tablename__ = "company_aliases"
    __table_args__ = (
        UniqueConstraint("alias", "stock_id", name="uq_company_aliases_alias_stock"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    alias_type: Mapped[str | None] = mapped_column(String(30))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    stock = relationship("Stock")


class StockNewsIngestionMeta(Base):
    __tablename__ = "stock_news_ingestion_meta"

    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), primary_key=True
    )
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    last_ingested_at = mapped_column(DateTime(timezone=True))
    last_article_at = mapped_column(DateTime(timezone=True))
    last_provider: Mapped[str | None] = mapped_column(String(50))
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    stock = relationship("Stock")


class NewsProviderQuotaState(Base):
    __tablename__ = "news_provider_quota_state"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    used_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limit_daily: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reset_at = mapped_column(DateTime(timezone=True), nullable=False)
    last_cursor: Mapped[str | None] = mapped_column(Text)
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class NewsIngestionRun(Base):
    __tablename__ = "news_ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="SET NULL"), index=True)
    started_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    finished_at = mapped_column(DateTime(timezone=True))
    articles_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    articles_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    links_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running", index=True)
    error: Mapped[str | None] = mapped_column(Text)

    stock = relationship("Stock")
