"""Фабрика веб-приложения — точка входа HTTP, а не его обработчик.

Лежит внутри `interface` только потому, что uvicorn получает путь строкой
(`app.interface.api.app:create_app`). По правилу ей положено импортировать
композицию, поэтому в tach.toml этот модуль помечен `unchecked` — он
единственный такой в слое входа, и это видно по имени.
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
    """`settings` и `entry_providers` — для тестов.

    Без них тест собирал бы ВТОРОЙ контейнер рядом и вешал второй middleware:
    два привходовых скоупа на запрос, две сессии, и контейнер из фабрики
    оставался бы висеть незакрытым (`ASGITransport` не проигрывает lifespan).
    Плюс контракт сборки жил бы в двух местах и разъехался бы.
    """
    _settings = settings or Settings.get()

    # Процессные ресурсы — движок, пулы, шифр — держит контейнер, и только он.
    # Точке входа остаётся отдать брокер: API не выполняет задачи, но ставит
    # их и опрашивает результат.
    broker = build_broker(_settings)

    # Реестр нужен и здесь, хотя задач этот процесс не выполняет: `find_task`
    # смотрит в РЕЕСТР СВОЕГО экземпляра брокера, и без регистрации постановка
    # падает `RuntimeError`.
    WorkerTasks.register(broker)

    providers = list(entry_providers)
    # Сборка — той же функцией, что у воркера, а не своим `make_async_container`
    # с тем же содержимым: контракт сборки, повторённый здесь, разъехался бы с
    # `build` молча — правку в `build` получили бы очередь и её тест, а HTTP-вход
    # нет.
    container = AppContainer.build(_settings, broker, *providers)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # Закрываем тот контейнер, который сами и собрали. Через
        # `app.state.dishka_container` было бы то же самое, но это уже
        # доставание контейнера из состояния — привычка, с которой начинается
        # service locator (проверяется `check_composition.py`).
        await container.close()
        # Брокер тоже наш: его создала эта фабрика, а контейнер получил
        # контекстом и потому не владеет. Без этой строки пулы Redis
        # (очередь плюс бэкенд результатов) на каждом рестарте API рвутся по
        # TCP вместо закрытия — тот же симптом, ради которого у воркера заведён
        # `close_container` на `WORKER_SHUTDOWN`. После контейнера, а не до:
        # закрываемый граф ещё вправе обратиться к очереди.
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

    # Заготовка под файлы, и она НЕ ЗАКРЫТА проверкой: всё, что окажется в
    # `uploads_dir`, отдаётся по прямой ссылке кому угодно. В шаблоне класть
    # туда нечему — ни одна строка не пишет в этот каталог, — поэтому сейчас
    # это пустая витрина. Опасной она становится в первый день, когда проект
    # начнёт складывать сюда что-то своё, считая раздачу защищённой. Тогда
    # выбор из двух: либо файлы отдаёт хендлер, проверяющий право на них, либо
    # сюда попадает только заведомо публичное. Не нужны файлы вовсе — эти
    # строки удаляются вместе с `uploads_dir` в `config.py` и томом
    # `uploads_data` в compose.
    Path(_settings.app.uploads_dir).mkdir(parents=True, exist_ok=True)
    app.mount(
        "/uploads",
        StaticFiles(directory=_settings.app.uploads_dir),
        name="uploads",
    )

    app.include_router(api_router)

    # После include_router: интеграция вешает middleware, открывающий привходовой
    # скоуп, и подставляет зависимости в хендлеры, помеченные FromDishka
    setup_dishka(container, app)

    return app
