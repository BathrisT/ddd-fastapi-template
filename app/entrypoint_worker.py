"""Точка входа воркера: `taskiq worker app.entrypoint_worker:broker`.

Обработчики вешает реестр, зависимости отдаёт контейнер — поэтому здесь нет ни
одного импорта-ради-побочного-эффекта. Список задач живёт в одном месте
(`composition/worker_tasks.py`), а не набором import-строк, которые расходятся
между воркером и шедулером молча.
"""

import sentry_sdk
from dishka.integrations.taskiq import setup_dishka
from taskiq import TaskiqEvents, TaskiqState

from app.composition.container import AppContainer
from app.composition.worker_tasks import WorkerTasks
from app.config import Settings
from app.infrastructure.worker.broker import build_broker
from app.logging import setup_logging

settings = Settings.get()

if settings.app.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.app.sentry_dsn,
        environment=settings.app.env,
        traces_sample_rate=0.2,
    )

setup_logging(debug=settings.app.env == "development")

broker = build_broker(settings)
container = AppContainer.build(settings, broker)

WorkerTasks.register(broker)
setup_dishka(container, broker)


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def close_container(_: TaskiqState) -> None:
    """Закрыть процессные ресурсы: движок, пулы Redis и httpx.

    У веб-входа это делает `lifespan`, у очереди симметрии не было бы —
    соединения к базе рвались бы по TCP на каждом деплое («unexpected EOF on
    client connection» в логах постгреса).
    """
    await container.close()
