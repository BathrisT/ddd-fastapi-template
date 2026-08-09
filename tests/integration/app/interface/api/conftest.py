import pytest
from dishka import Provider, Scope, provide
from httpx import ASGITransport, AsyncClient

from app.application.ports.event_publisher import EventPublisher
from app.config import Settings
from app.infrastructure.events.noop_publisher import NoopEventPublisher
from app.interface.api.app import create_app


class TestEntryProvider(Provider):
    """Вход теста: события никуда не уходят, остальной граф настоящий.

    Ровно то же место, куда веб-вход кладёт свои провайдеры. Публикатор
    подменяется потому, что настоящий ставит задачу в очередь, а Redis в
    интеграционном прогоне не поднимается: тест проверяет HTTP-вход и базу, а
    не транспорт очереди.
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

    Второй контейнер рядом означал бы второй middleware, два привходовых
    скоупа на запрос и осиротевший движок.

    Гасится СВОИМ lifespan, а не разбором `app.state` руками. `ASGITransport`
    lifespan не проигрывает, поэтому запускаем его здесь явно. Через
    `app.state.dishka_container` было бы короче на строку и неверно дважды:
    во-первых, доставание контейнера из состояния — привычка, с которой
    начинается service locator (её и стережёт `check_composition.py`, просто
    не заглядывая в `tests/`); во-вторых, контейнером владение не
    исчерпывается — фабрика держит ещё и брокер, и закрыть его снаружи нечем.
    Так teardown в тестах и в проде — один и тот же код, а не две расходящиеся
    версии, из которых читатель скопирует ту, что попалась.
    """
    app = create_app(test_settings, entry_providers=entry_providers)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(fastapi_app) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as c:
        yield c
