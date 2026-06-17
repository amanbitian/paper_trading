from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("user_name", name="uq_users_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    user_name: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    starting_cash: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=1000000)
    current_cash: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=1000000)
    risk_profile: Mapped[str] = mapped_column(String(50), nullable=False, default="moderate")
    email_alerts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    credential = relationship("UserCredential", back_populates="user", cascade="all, delete-orphan", uselist=False)
    auth_sessions = relationship("AuthSession", back_populates="user", cascade="all, delete-orphan")
    password_reset_tokens = relationship("PasswordResetToken", back_populates="user", cascade="all, delete-orphan")
    portfolios = relationship("Portfolio", back_populates="user", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="user")
    paper_orders = relationship("PaperOrder", back_populates="user")
    paper_trades = relationship("PaperTrade", back_populates="user")
    strategies = relationship("UserStrategy", back_populates="user")
