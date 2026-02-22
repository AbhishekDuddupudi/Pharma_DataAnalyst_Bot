"""
Centralized application configuration – loaded from environment variables.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All env-driven configuration in one place."""

    # ── App ───────────────────────────────────────────────────────
    APP_NAME: str = "Pharma Data Analyst Bot"
    APP_VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "DEBUG"

    # ── Database ──────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://pharma:pharma_secret@db:5432/pharma_db"

    # ── CORS ──────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:5173"

    # ── Auth / Sessions ───────────────────────────────────────────
    SESSION_TTL_DAYS: int = 7

    # ── OpenAI / LLM ─────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # ── Agent / Streaming ─────────────────────────────────────────
    STREAM_DEMO_DELAY_MS: int = 0
    SQL_MAX_RETRIES: int = 2
    SQL_MAX_ROWS: int = 100

    # ── Langfuse Observability ─────────────────────────────────
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # ── Helpers ───────────────────────────────────────────────────
    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated origins into a list."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
