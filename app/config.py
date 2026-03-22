# app/config.py
from pathlib import Path
from pydantic_settings import SettingsConfigDict, BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    # Время жизни файлов (в секундах)
    ttl_seconds: int = 7200

    # Базовый URL для скачивания
    public_base: str = "http://192.168.1.6"

    # Хост и порт
    host: str = "127.0.0.1"
    port: int = 8000

    # Пути
    templates_dir: Path = Path("templates")
    storage_dir: Path = Path("storage")

    # Валидация путей — делаем абсолютными относительно корня проекта
    @field_validator("templates_dir", "storage_dir", mode="before")
    @classmethod
    def make_absolute(cls, v: str | Path) -> Path:
        if isinstance(v, str):
            v = Path(v)
        if not v.is_absolute():
            v = Path(__file__).parent.parent / v
        return v.resolve()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DOCGEN_",
        extra="ignore"
    )


# Глобальный экземпляр настроек
settings = Settings()