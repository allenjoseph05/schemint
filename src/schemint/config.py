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
    host: str = "0.0.0.0"  # nosec B104
    port: int = 8000

    # API
    api_v1_prefix: str = "/api/v1"

    # Claude AI settings
    claude_api_key: str | None = None
    claude_model: str = "claude-sonnet-4-20250514"
    claude_model_simple: str = "claude-haiku-4-5-20251001"  # 1-3 tables
    claude_model_complex: str = "claude-sonnet-4-5-20250929"  # 16+ tables
    claude_max_agent_turns: int = 10  # Max tool-use round trips for agent

    # Analysis
    max_sql_length: int = 100000  # 100KB
    default_database_type: str = "mysql"

    # Database (Memory Store)
    database_url: str | None = None  # PostgreSQL connection string
    # Example: postgresql://user:password@localhost:5432/schemint

    # Drift agent controller
    webhook_url: str | None = None  # Slack webhook URL for drift notifications
    notification_webhook_headers: str = "{}"  # JSON string of extra headers
    github_token: str | None = None  # GitHub token for CI adapter
    github_repo: str | None = None  # GitHub repo (owner/name) for CI adapter
    github_commit_sha: str | None = None  # Default commit SHA for CI status checks
    github_default_reviewer: str | None = None  # GitHub username to request review from
    drift_auto_approve_severities: str = "low,medium"  # Comma-separated
    drift_max_retries: int = 2  # Max retry attempts on verification failure

    # SQL execution (Phase 5 SQLRunner)
    target_db_url: str | None = None  # Target DB connection for schema remediation SQL
    sql_dry_run: bool = False  # If True, log SQL but do not execute

    # AI quality (Milestone 5)
    enable_plan_critique: bool = False  # If True, run a second Haiku call to critique plans

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
