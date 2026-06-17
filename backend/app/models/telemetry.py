from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SearchQueryLog(Base):
    __tablename__ = "search_query_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    search_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    filter_name: Mapped[str | None] = mapped_column(String(50))
    filter_value: Mapped[str | None] = mapped_column(String(120))
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ok", index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class AiActionLog(Base):
    __tablename__ = "ai_action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="api")
    action_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(120), nullable=False)
    http_method: Mapped[str] = mapped_column(String(10), nullable=False, default="POST")
    model_name: Mapped[str | None] = mapped_column(String(120))
    ollama_base_url: Mapped[str | None] = mapped_column(String(200))
    ollama_connected: Mapped[bool | None] = mapped_column(Boolean)
    request_payload = mapped_column(JSONB)
    response_payload = mapped_column(JSONB)
    llm_prompt: Mapped[str | None] = mapped_column(Text)
    llm_response: Mapped[str | None] = mapped_column(Text)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ok", index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
