from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    telegram_api_id: int
    telegram_api_hash: SecretStr
    telegram_session_path: Path = Path("/app/session/downloader")
    allowed_chat_ids: list[int] = Field(default_factory=list)
    include_saved_messages: bool = False

    database_path: Path = Path("/app/database/downloads.db")
    log_dir: Path = Path("/app/logs")
    download_root: Path = Path("/downloads")
    concurrent_downloads: int = Field(default=1, ge=1, le=16)
    max_file_size_gb: float = Field(default=0, ge=0)
    min_free_space_gb: float = Field(default=5, ge=0)
    bandwidth_limit_mbps: float = Field(default=0, ge=0)
    max_retries: int = Field(default=5, ge=0, le=50)
    retry_base_seconds: float = Field(default=10, ge=0.1)

    status_replies_enabled: bool = False
    progress_update_interval: int = Field(default=15, ge=5)

    jellyfin_url: str | None = None
    jellyfin_api_key: SecretStr | None = None
    jellyfin_refresh_enabled: bool = False

    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = Field(default=8787, ge=1, le=65535)
    dashboard_username: str | None = None
    dashboard_password: SecretStr | None = None
    log_level: str = "INFO"

    @field_validator("allowed_chat_ids", mode="before")
    @classmethod
    def parse_chat_ids(cls, value: object) -> object:
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, or ERROR")
        return value

    def ensure_directories(self) -> None:
        for path in (self.database_path.parent, self.log_dir, self.telegram_session_path.parent):
            path.mkdir(parents=True, exist_ok=True)
        for category in (
            "movies",
            "tv",
            "videos",
            "audio",
            "images",
            "documents",
            "archives",
            "other",
            "incomplete",
        ):
            (self.download_root / category).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
