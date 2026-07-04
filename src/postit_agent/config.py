from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "PostIt Agent"
    environment: str = "development"
    secret_key: str = "development-secret"
    token_ttl_minutes: int = 60 * 24 * 7
    public_base_url: str = "http://localhost:8000"

    database_path: Path = BASE_DIR / "data" / "postit_agent.sqlite3"
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

    demo_mode: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="POSTIT_",
        extra="ignore",
    )

    def ensure_directories(self) -> None:
        for path in (self.database_path.parent, self.storage_dir, self.exports_dir, self.feeds_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
