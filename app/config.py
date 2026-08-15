from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application configuration.

    Explicit environment values win. Paths not explicitly supplied are derived from
    ``TMD_DATA_DIR`` and ``TMD_DOWNLOAD_DIR``, then fall back to container-safe defaults.
    Legacy environment names remain accepted for existing deployments.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    telegram_api_id: int = Field(validation_alias="TELEGRAM_API_ID")
    telegram_api_hash: SecretStr = Field(validation_alias="TELEGRAM_API_HASH")
    telegram_phone: str | None = Field(default=None, validation_alias="TELEGRAM_PHONE")
    telegram_session_path: Path | None = Field(
        default=None, validation_alias="TELEGRAM_SESSION_PATH"
    )
    session_dir: Path | None = Field(default=None, validation_alias="TMD_SESSION_DIR")
    session_name: str = Field(default="downloader", validation_alias="TMD_SESSION_NAME")
    allowed_chat_ids: list[int] = Field(default_factory=list)
    include_saved_messages: bool = False

    data_dir: Path = Field(default=Path("/data"), validation_alias="TMD_DATA_DIR")
    database_path: Path | None = Field(
        default=None, validation_alias=AliasChoices("TMD_DATABASE_PATH", "DATABASE_PATH")
    )
    config_dir: Path | None = Field(default=None, validation_alias="TMD_CONFIG_DIR")
    log_dir: Path | None = Field(
        default=None, validation_alias=AliasChoices("TMD_LOG_DIR", "LOG_DIR")
    )
    download_root: Path = Field(
        default=Path("/downloads"),
        validation_alias=AliasChoices("TMD_DOWNLOAD_DIR", "DOWNLOAD_ROOT"),
    )
    temp_dir: Path | None = Field(default=None, validation_alias="TMD_TEMP_DIR")

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

    dashboard_host: str = Field(
        default="0.0.0.0", validation_alias=AliasChoices("TMD_WEB_HOST", "DASHBOARD_HOST")
    )
    dashboard_port: int = Field(
        default=8787,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("TMD_WEB_PORT", "DASHBOARD_PORT"),
    )
    dashboard_username: str | None = None
    dashboard_password: SecretStr | None = None
    log_level: str = "INFO"

    @model_validator(mode="after")
    def derive_paths(self) -> Settings:
        self.session_dir = self.session_dir or self.data_dir / "session"
        self.telegram_session_path = (
            self.telegram_session_path or self.session_dir / self.session_name
        )
        self.database_path = self.database_path or self.data_dir / "db" / "downloads.db"
        self.config_dir = self.config_dir or self.data_dir / "config"
        self.log_dir = self.log_dir or self.data_dir / "logs"
        self.temp_dir = self.temp_dir or self.download_root / "incomplete"
        return self

    @field_validator("session_name")
    @classmethod
    def validate_session_name(cls, value: str) -> str:
        value = value.strip()
        if not value or value in {".", ".."} or Path(value).name != value:
            raise ValueError("TMD_SESSION_NAME must be a filename without a directory")
        return value.removesuffix(".session")

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
        paths = (
            self.database_path.parent,
            self.session_dir,
            self.config_dir,
            self.log_dir,
            self.download_root,
            self.temp_dir,
        )
        try:
            for path in paths:
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
            ):
                (self.download_root / category).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Unable to create or access runtime directory: {exc}") from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()
