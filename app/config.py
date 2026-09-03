from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class App(BaseModel):
    # `Literal`, а не `str`: опечатка `APP__ENV=prod` прошла бы валидацию и
    # дала прод с автоперезагрузкой — внешне рабочий. Здесь она роняет старт.
    env: Literal["development", "production"] = "development"
    sentry_dsn: str = ""
    # Абсолютные ссылки наружу. Пусто по умолчанию: обязательное поле без
    # потребителя учит заполнять окружение наугад. Отказ на пустом — дело
    # первого потребителя, только он знает цену.
    api_base_url: str = ""
    frontend_base_url: str = ""
    # Только JSON-массив: перечисление через запятую роняет `Settings()`, а с
    # ним все три точки входа — настройки читаются на импорте.
    cors_origins: list[str] = []
    # Fernet для полей, шифруемых на хранении. Сгенерировать:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str
    # `X-Api-Key`. Пусто — вход не проверяется вовсе, чтобы шаблон поднимался
    # из коробки; в проде это открытые ручки, и верификатор пишет о них в лог.
    api_key: str = ""
    uploads_dir: str = "uploads"


class Database(BaseModel):
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str
    name: str = "app"
    pool_size: int = 10
    max_overflow: int = 5
    pool_recycle: int = 1800

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class Redis(BaseModel):
    host: str = "localhost"
    port: int = 6379
    password: str = ""
    db: int = 0

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class LLM(BaseModel):
    """Ключ общий на приложение; свой ключ арендатора жил бы в БД."""

    timeout: int = 60
    proxy: str | None = None  # e.g. socks5://user:pass@host:port or http://host:port
    api_key: str = ""
    base_url: str | None = None
    model: str = "gpt-4o-mini"

    @field_validator("proxy", "base_url", mode="before")
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        """`LLM__PROXY=` — это «нет прокси», а не прокси с пустым адресом.

        Пустую строку обе библиотеки принимают всерьёз: httpx падает «Unknown
        scheme for proxy URL», а AsyncOpenAI шлёт запрос по относительному пути.
        """
        return None if value == "" else value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app: App
    database: Database
    redis: Redis = Redis()
    llm: LLM = LLM()

    @staticmethod
    @lru_cache
    def get() -> Settings:
        return Settings()  # type: ignore[call-arg]  # pydantic-settings populates required fields from env
