import pytest
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient

from app.application.ports.event_publisher import EventPublisher
from app.config import Settings
from app.infrastructure.events.noop_publisher import NoopEventPublisher
from app.interface.api.app import create_app


class TestEntryProvider(Provider):
    """Вход теста: события никуда не уходят, остальной граф настоящий.

    Настоящий публикатор ставит задачу в очередь, а Redis тут не поднимается:
    проверяем HTTP-вход и базу, не транспорт.
    """

    @provide(scope=Scope.APP, override=True)
    def event_publisher(self) -> EventPublisher:
        return NoopEventPublisher()


@pytest.fixture
def entry_providers() -> list[Provider]:
    """Что тест подменяет в графе. Переопредели фикстуру в своём модуле."""
    return [TestEntryProvider()]


@pytest.fixture
async def fastapi_app(test_settings: Settings, entry_providers: list[Provider]):
    """Приложение с ОДНИМ контейнером — тем, что собрала фабрика.

    Второй означал бы второй middleware и два скоупа на запрос. Гасится СВОИМ
    lifespan, а не разбором `app.state`: фабрика держит ещё и брокер.
    """
    app = create_app(test_settings, entry_providers=entry_providers)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(fastapi_app) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as c:
        yield c
