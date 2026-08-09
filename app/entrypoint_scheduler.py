"""Точка входа шедулера: `taskiq scheduler app.entrypoint_scheduler:scheduler`.

Обработчики вешает реестр, расписание лежит в `interface/worker/schedule.py`.
Контейнер шедулеру не нужен: он ничего не выполняет, только кладёт сообщения в
очередь по времени — тела задач разбирает воркер, у него зависимости и есть.
"""

import sentry_sdk
from taskiq import TaskiqScheduler
from taskiq.abc.schedule_source import ScheduleSource
from taskiq.scheduler.scheduled_task import ScheduledTask

from app.composition.worker_tasks import WorkerTasks
from app.config import Settings
from app.infrastructure.worker.broker import build_broker
from app.interface.worker.schedule import Schedule
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

WorkerTasks.register(broker)


class AppScheduleSource(ScheduleSource):
    async def get_schedules(self) -> list[ScheduledTask]:
        return Schedule.scheduled_tasks()


scheduler = TaskiqScheduler(broker=broker, sources=[AppScheduleSource()])
