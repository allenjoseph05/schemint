"""Application configuration."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "Schemint"
    app_version: str = "0.1.0"
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # API
    api_v1_prefix: str = "/api/v1"

    # AI (Claude)
    claude_api_key: str | None = None
    claude_model: str = "claude-sonnet-4-20250514"

    # Analysis
    max_sql_length: int = 100000  # 100KB
    default_database_type: str = "mysql"

    @property
    def is_development(self) -> bool:
        return self.env == "development"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def ai_enabled(self) -> bool:
        return self.claude_api_key is not None


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
