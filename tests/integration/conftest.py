"""Фикстуры, которым нужна настоящая база.

Контейнер поднимается один раз на сессию, миграции прогоняются один раз, а
таблицы чистятся между тестами — параллельный прогон (`-n auto`) держит по
контейнеру на воркер, поэтому чистка не мешает соседям.

Настройки собираются поимённо, а НЕ читаются из `.env`: тест обязан ходить в
свой одноразовый контейнер. Подхвати он окружение разработчика — `alembic
upgrade head` и `TRUNCATE` уехали бы в рабочую базу.
"""

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.config import LLM, App, Database, Redis, Settings


@pytest.fixture(scope="session")
def postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def test_settings(postgres: PostgresContainer) -> Settings:
    return Settings(
        app=App(
            env="development",
            api_base_url="http://localhost:8000",
            frontend_base_url="http://localhost:5173",
            fernet_key="dGVzdC1mZXJuZXQta2V5LTMyLWJ5dGVzLWxvbmchISE=",
            # Непустой намеренно: с пустым ключом верификатор пропускает
            # всех, и тест на отказ проверял бы отсутствие проверки.
            api_key="test-api-key",
        ),
        database=Database(
            host=postgres.get_container_host_ip(),
            port=int(postgres.get_exposed_port(5432)),
            user=postgres.username,
            password=postgres.password,
            name=postgres.dbname,
        ),
        redis=Redis(),
        llm=LLM(),
    )


@pytest.fixture(scope="session", autouse=True)
def run_migrations(test_settings: Settings) -> None:
    os.environ["ALEMBIC_DB_URL"] = test_settings.database.url
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def engine(test_settings: Settings, run_migrations: None):
    eng = create_async_engine(test_settings.database.url, pool_size=5, max_overflow=0)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
async def session_factory(engine):
    # Те же аргументы, что у боевой фабрики в `ProcessProvider.session_factory`,
    # и `autoflush=False` здесь особенно важен: под автосбросом репозиторий,
    # забывший явный `flush()`, всё равно зелёный — незаметно отправленная
    # вставка приезжает внутри следующего произвольного запроса. Разойдись эти
    # две фабрики, интеграционные тесты проверяли бы семантику, которой в
    # проде нет.
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture(autouse=True)
async def clean_tables(engine):
    """Чистка до и после теста.

    До — тоже намеренно: упавший тест не обязан прибирать за собой, а следующий
    не должен видеть его мусор.
    """
    _truncate = text("TRUNCATE users CASCADE")
    async with engine.begin() as conn:
        await conn.execute(_truncate)
    yield
    async with engine.begin() as conn:
        await conn.execute(_truncate)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s
        await s.rollback()
