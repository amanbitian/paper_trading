from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_env: str = Field(default="local", alias="APP_ENV")
    database_url: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/paper_trading",
        alias="DATABASE_URL",
    )
    jwt_secret_key: str = Field(default="change_me", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(
        default=1440, alias="ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    auto_migrate_on_start: bool = Field(default=False, alias="AUTO_MIGRATE_ON_START")
    auth_debug_log_password_checks: bool = Field(
        default=False, alias="AUTH_DEBUG_LOG_PASSWORD_CHECKS"
    )
    # --- Persistent ("remember me") browser session cookie ---
    # The web UI logs in once and stays logged in until explicit logout, using a
    # long-lived httpOnly cookie with sliding renewal (industry-standard pattern).
    session_cookie_name: str = Field(default="pt_session", alias="SESSION_COOKIE_NAME")
    # Absolute cap on a session's life (days). Renewed on activity, so an active
    # user is effectively "logged in until logout"; an idle session expires after this.
    session_max_age_days: int = Field(default=365, alias="SESSION_MAX_AGE_DAYS")
    # Re-extend the session when it has been used and its remaining life has
    # dropped by more than this many days (limits renewal writes to ~1/day/user).
    session_renew_after_days: int = Field(default=1, alias="SESSION_RENEW_AFTER_DAYS")
    # Send the cookie only over HTTPS. Keep False for local http dev; set True in prod.
    session_cookie_secure: bool = Field(default=False, alias="SESSION_COOKIE_SECURE")
    session_cookie_samesite: str = Field(default="lax", alias="SESSION_COOKIE_SAMESITE")
    yfinance_default_period: str = Field(default="1y", alias="YFINANCE_DEFAULT_PERIOD")
    yfinance_default_interval: str = Field(default="1d", alias="YFINANCE_DEFAULT_INTERVAL")
    market_movers_max_abs_daily_change_pct: float = Field(
        default=50.0, alias="MARKET_MOVERS_MAX_ABS_DAILY_CHANGE_PCT"
    )
    market_movers_max_candle_gap_days: int = Field(
        default=7, alias="MARKET_MOVERS_MAX_CANDLE_GAP_DAYS"
    )
    market_movers_require_volume: bool = Field(
        default=True, alias="MARKET_MOVERS_REQUIRE_VOLUME"
    )
    smtp_host: str = Field(default="smtp.gmail.com", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from_name: str = Field(default="Paper Trading App", alias="SMTP_FROM_NAME")
    email_alerts_enabled: bool = Field(default=False, alias="EMAIL_ALERTS_ENABLED")
    password_reset_token_expire_minutes: int = Field(
        default=30, alias="PASSWORD_RESET_TOKEN_EXPIRE_MINUTES"
    )
    app_base_url: str = Field(default="http://localhost:8000", alias="APP_BASE_URL")
    ollama_base_url: str = Field(
        default="http://localhost:11434", alias="OLLAMA_BASE_URL"
    )
    ollama_default_model: str = Field(default="qwen3:14b", alias="OLLAMA_DEFAULT_MODEL")
    ollama_fallback_model: str = Field(default="qwen3:8b", alias="OLLAMA_FALLBACK_MODEL")
    ollama_timeout_seconds: int = Field(default=60, alias="OLLAMA_TIMEOUT_SECONDS")
    ollama_max_tokens: int = Field(default=1500, alias="OLLAMA_MAX_TOKENS")
    ai_features_enabled: bool = Field(default=False, alias="AI_FEATURES_ENABLED")
    llm_cache_ttl_hours: int = Field(default=24, alias="LLM_CACHE_TTL_HOURS")
    marketaux_api_token: str = Field(default="", alias="MARKETAUX_API_TOKEN")
    alpha_vantage_api_key: str = Field(default="", alias="ALPHA_VANTAGE_API_KEY")
    news_request_timeout_seconds: int = Field(default=12, alias="NEWS_REQUEST_TIMEOUT_SECONDS")
    news_on_demand_limit: int = Field(default=10, alias="NEWS_ON_DEMAND_LIMIT")
    news_marketaux_daily_limit: int = Field(default=100, alias="NEWS_MARKETAUX_DAILY_LIMIT")
    news_alpha_vantage_daily_limit: int = Field(default=25, alias="NEWS_ALPHA_VANTAGE_DAILY_LIMIT")
    finnhub_api_key: str = Field(default="", alias="FINNHUB_API_KEY")
    news_finnhub_daily_limit: int = Field(default=0, alias="NEWS_FINNHUB_DAILY_LIMIT")
    strategy_explainer_refresh_on_start: bool = Field(
        default=False, alias="STRATEGY_EXPLAINER_REFRESH_ON_START"
    )
    strategy_explainer_startup_exchange: str = Field(
        default="NSE", alias="STRATEGY_EXPLAINER_STARTUP_EXCHANGE"
    )
    strategy_explainer_startup_limit: int = Field(
        default=100, alias="STRATEGY_EXPLAINER_STARTUP_LIMIT"
    )
    strategy_explainer_startup_delay_seconds: int = Field(
        default=120, alias="STRATEGY_EXPLAINER_STARTUP_DELAY_SECONDS"
    )
    stock_detail_snapshot_refresh_on_start: bool = Field(
        default=False, alias="STOCK_DETAIL_SNAPSHOT_REFRESH_ON_START"
    )
    stock_detail_snapshot_startup_exchange: str = Field(
        default="NSE", alias="STOCK_DETAIL_SNAPSHOT_STARTUP_EXCHANGE"
    )
    stock_detail_snapshot_startup_limit: int = Field(
        default=25, alias="STOCK_DETAIL_SNAPSHOT_STARTUP_LIMIT"
    )
    stock_detail_snapshot_startup_delay_seconds: int = Field(
        default=180, alias="STOCK_DETAIL_SNAPSHOT_STARTUP_DELAY_SECONDS"
    )

    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.app_env.lower() == "production" and self.jwt_secret_key == "change_me":
            raise ValueError("JWT_SECRET_KEY must be changed when APP_ENV=production")
        if self.app_env.lower() == "production" and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE must be true when APP_ENV=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
