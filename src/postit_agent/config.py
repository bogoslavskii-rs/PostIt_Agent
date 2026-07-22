from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "PostIt Agent"
    environment: str = "development"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    secret_key: str = "development-secret"
    access_token_expire_minutes: int = 60 * 24 * 7
    public_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    database_path: Path = BASE_DIR / "data" / "postit_agent.sqlite3"
    database_url: str | None = None
    redis_url: str | None = None
    storage_dir: Path = BASE_DIR / "data" / "storage"
    exports_dir: Path = BASE_DIR / "data" / "exports"
    feeds_dir: Path = BASE_DIR / "data" / "feeds"
    static_dir: Path = BASE_DIR / "src" / "postit_agent" / "static"

    ai_provider: str = "mock"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout_seconds: int = 90
    ollama_temperature: float = 0.2
    ollama_top_p: float = 0.9
    ollama_repeat_penalty: float = 1.1
    ollama_num_ctx: int = 8192
    ollama_num_predict: int = 2048

    stt_provider: str = "mock"
    whisper_cpp_binary: str | None = None
    whisper_cpp_model_path: str | None = None

    max_upload_size_mb: int = 30
    allowed_audio_formats: str = "wav,mp3,m4a,ogg,webm,txt"
    allowed_image_formats: str = "jpg,jpeg,png,webp"

    cian_create_url: str | None = None
    yandex_realty_create_url: str | None = None
    avito_create_url: str | None = None
    youla_create_url: str | None = None
    domclick_create_url: str | None = None

    demo_mode: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="POSTIT_",
        extra="ignore",
    )

    def ensure_directories(self) -> None:
        for path in (self.database_path.parent, self.storage_dir, self.exports_dir, self.feeds_dir):
            path.mkdir(parents=True, exist_ok=True)

    def upload_limit_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    def allowed_audio_formats_set(self) -> set[str]:
        return {item.strip().lower().lstrip(".") for item in self.allowed_audio_formats.split(",") if item.strip()}

    def allowed_image_formats_set(self) -> set[str]:
        return {item.strip().lower().lstrip(".") for item in self.allowed_image_formats.split(",") if item.strip()}

    def cors_origins_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def platform_create_url(self, platform: str) -> str | None:
        mapping = {
            "cian": self.cian_create_url,
            "yandex_realty": self.yandex_realty_create_url,
            "avito": self.avito_create_url,
            "youla": self.youla_create_url,
            "domclick": self.domclick_create_url,
        }
        return mapping.get(platform)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
