"""Сборка контейнера: общая часть плюс провайдеры конкретного входа.

Общая часть одна на все входы — процессные ресурсы, сессия, репозитории,
сценарии. Различаться имеет право ровно то, что у входов действительно разное:
откуда берётся арендатор, что кладёт в скоуп транспорт. Всё остальное общее, и
подключение нового интерфейса стоит одного маленького провайдера, а не второй
сборки графа (правило 6 композиции).

Правило целиком — docs/rules/композиция-и-скоупы.md
"""

from dishka import AsyncContainer, Provider, make_async_container
from taskiq import AsyncBroker

from app.composition.providers.events import EventProvider
from app.composition.providers.process import ProcessProvider
from app.composition.providers.repositories import RepositoryProvider
from app.composition.providers.request import RequestProvider
from app.composition.providers.use_cases import UseCaseProvider
from app.config import Settings


class AppContainer:
    @staticmethod
    def shared() -> list[Provider]:
        """Всё, что не зависит от того, каким входом пришла работа."""
        return [
            ProcessProvider(),
            RequestProvider(),
            EventProvider(),
            RepositoryProvider(),
            UseCaseProvider(),
        ]

    @staticmethod
    def build(
        settings: Settings, broker: AsyncBroker, *entry_providers: Provider
    ) -> AsyncContainer:
        """Брокер приходит снаружи по той же причине, что и настройки: точка
        входа уже держит его на уровне модуля — этого требует CLI taskiq."""
        return make_async_container(
            *AppContainer.shared(),
            *entry_providers,
            context={Settings: settings, AsyncBroker: broker},
        )
