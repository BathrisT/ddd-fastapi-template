from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class App(BaseModel):
    # `Literal`, а не `str`, и причина не в аккуратности. Из этого поля точки
    # входа выводят ДВА решения с противоположной стороной по умолчанию:
    # `debug = env == "development"` промахивается в сторону прода, а
    # `reload = env != "production"` — в сторону дева. Опечатка `APP__ENV=prod`
    # прошла бы валидацию строки и дала бы прод с автоперезагрузкой — внешне
    # рабочий. Здесь она роняет процесс на старте, где видна сразу. Нужно
    # третье окружение — оно дописывается сюда, и тогда же автор смотрит на оба
    # вывода.
    env: Literal["development", "production"] = "development"
    sentry_dsn: str = ""
    # Публичные адреса — для абсолютных ссылок наружу (письма, вебхуки, редиректы
    # в UI). Пусто по умолчанию, и это намеренно: в шаблоне их не читает ни одна
    # строка, а обязательное поле без потребителя учит заполнять окружение
    # наугад. Появится первый потребитель — он и обязан отказать на пустом
    # значении, потому что только он знает, что ссылка без адреса бессмысленна.
    api_base_url: str = ""
    frontend_base_url: str = ""
    # Разрешённые источники CORS. В окружении — JSON-массив
    # (`APP__CORS_ORIGINS=["https://app.example.com"]`), как в `.env.example`, и
    # никак иначе: перечисление через запятую pydantic-settings не разбирает, а
    # роняет `Settings()` — то есть падает не CORS, а разом все три точки входа,
    # потому что настройки читаются на импорте. Пустой список — CORS выключен.
    cors_origins: list[str] = []
    # Fernet key for encrypting sensitive fields at rest (tokens, API keys).
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str
    # Ключ доступа к API, заголовок `X-Api-Key`. Пусто — вход не проверяется
    # вовсе: шаблон обязан подниматься из коробки, а ключ, придуманный за
    # пользователя, всё равно был бы известен всем. В проде пустое значение
    # означает открытые ручки, и верификатор говорит об этом в логе на каждом
    # отказе — молча пропускать он не имеет права.
    api_key: str = ""
    # Directory for uploaded files. Override via APP__UPLOADS_DIR env var.
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
    """Infrastructure settings for the LLM HTTP client.

    Ключ здесь — общий на приложение. Если у арендатора может быть свой,
    он живёт в БД, а эти значения остаются запасным вариантом.
    """

    timeout: int = 60
    proxy: str | None = None  # e.g. socks5://user:pass@host:port or http://host:port
    api_key: str = ""
    base_url: str | None = None
    model: str = "gpt-4o-mini"

    @field_validator("proxy", "base_url", mode="before")
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        """`LLM__PROXY=` в окружении — это «нет прокси», а не прокси с пустым адресом.

        Для `str | None` pydantic-settings отдаёт ровно то, что стоит в env,
        то есть `""`, и оно проходит валидацию как строка. Дальше `""`
        доезжает до библиотек, и обе воспринимают его всерьёз:
        `httpx.AsyncClient(proxy="")` падает «Unknown scheme for proxy URL»
        прямо в конструкторе `OpenAiService`, а `AsyncOpenAI` подставляет
        официальный адрес только при `None` — на `""` запрос уходит по
        относительному пути. Оба ключа лежат в `.env.example` пустыми с
        подписью «пусто — значит по умолчанию», а README велит копировать его
        как есть: без этой нормализации быстрый старт из README ломает каждую
        задачу, обращающуюся к модели, при живом и зелёном API.
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
