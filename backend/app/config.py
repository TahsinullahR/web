"""Application configuration loaded from environment variables (.env supported).

No secrets are ever sent to the frontend; this module is only imported by the
backend process.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # OpenRouter
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    openrouter_model: str = Field(default="openrouter/free", alias="OPENROUTER_MODEL")

    # Database (SQLite now; swap URL for PostgreSQL later — see docs/ARCHITECTURE.md)
    database_url: str = Field(default="sqlite:///./nova.db", alias="DATABASE_URL")

    # Server
    cors_origins: str = Field(
        default="http://localhost:3000", alias="CORS_ORIGINS"
    )
    request_timeout_seconds: float = Field(
        default=60.0, alias="OPENROUTER_TIMEOUT_SECONDS"
    )
    max_retries: int = Field(default=3, alias="OPENROUTER_MAX_RETRIES")

    # Uploads
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, alias="MAX_UPLOAD_BYTES")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
