"""Фабрика веб-приложения — точка входа HTTP, а не его обработчик.

Лежит в `interface` только потому, что uvicorn получает путь строкой. Ей
положено импортировать композицию, поэтому в tach.toml она `unchecked` —
единственная такая в слое входа.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from dishka import Provider
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.composition.container import AppContainer
from app.composition.worker_tasks import WorkerTasks
from app.config import Settings
from app.infrastructure.worker.broker import build_broker
from app.interface.api.exception_handlers import register_exception_handlers
from app.interface.api.routes import router as api_router


def create_app(
    settings: Settings | None = None,
    entry_providers: Sequence[Provider] = (),
) -> FastAPI:
    """`settings` и `entry_providers` — для тестов: без них тест собрал бы
    ВТОРОЙ контейнер рядом, с двумя скоупами и сессиями на запрос.
    """
    _settings = settings or Settings.get()

    # Процессные ресурсы держит контейнер; входу остаётся брокер.
    broker = build_broker(_settings)

    # `find_task` смотрит в реестр СВОЕГО брокера: без регистрации
    # постановка падает `RuntimeError`.
    WorkerTasks.register(broker)

    providers = list(entry_providers)
    # Сборка той же функцией, что у воркера: повторённый здесь контракт
    # разъехался бы с `build` молча — правку получил бы не всякий вход.
    container = AppContainer.build(_settings, broker, *providers)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # Свой контейнер: доставать его из `app.state` — начало locator'а.
        await container.close()
        # Брокер тоже наш: контейнер получил его контекстом и не владеет им.
        # Без этой строки пулы Redis рвутся по TCP на каждом рестарте. После
        # контейнера, а не до: закрываемый граф ещё вправе звать очередь.
        await broker.shutdown()

    app = FastAPI(
        title="App API",
        version="0.1.0",
        lifespan=lifespan,
    )

    if _settings.app.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_settings.app.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)

    # НЕ ЗАКРЫТО проверкой: всё, что попадёт в `uploads_dir`, отдаётся по
    # прямой ссылке кому угодно. Сейчас витрина пуста — сюда никто не пишет.
    # Начнёт писать: либо файлы отдаёт проверяющий право хендлер, либо сюда
    # кладут заведомо публичное. Не нужны — удаляются вместе с `uploads_dir`.
    Path(_settings.app.uploads_dir).mkdir(parents=True, exist_ok=True)
    app.mount(
        "/uploads",
        StaticFiles(directory=_settings.app.uploads_dir),
        name="uploads",
    )

    app.include_router(api_router)

    # После include_router: вешает middleware скоупа и раздаёт FromDishka.
    setup_dishka(container, app)

    return app
