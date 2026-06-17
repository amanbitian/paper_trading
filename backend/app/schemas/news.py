from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NewsArticleRead(BaseModel):
    id: int
    headline: str
    summary: str | None = None
    url: str
    source: str | None = None
    source_domain: str | None = None
    provider: str
    published_at: datetime
    sentiment_score: float | None = None
    match_confidence: float | None = None
    match_method: str | None = None


class NewsRefreshRead(BaseModel):
    status: str
    stock_id: int | None = None
    run_id: int | None = None
    reason: str | None = None
    articles_in: int | None = None
    articles_new: int | None = None
    links_new: int | None = None
    providers: list[dict[str, Any]] = Field(default_factory=list)
    news: list[NewsArticleRead] = Field(default_factory=list)


class NewsPriorityRefreshRead(BaseModel):
    status: str
    stocks_selected: int
    links_new: int
    results: list[dict[str, Any]] = Field(default_factory=list)


class NewsSummaryRead(BaseModel):
    articles: int
    links: int
    stocks_with_news: int
    latest_article_at: datetime | None = None


class NewsIngestionRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    mode: str
    stock_id: int | None = None
    started_at: datetime
    finished_at: datetime | None = None
    articles_in: int
    articles_new: int
    links_new: int
    status: str
    error: str | None = None
