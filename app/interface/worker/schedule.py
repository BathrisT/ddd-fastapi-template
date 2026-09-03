"""Расписание: что и как часто дёргать.

Не в композиции (её импортируют ВСЕ входы, а расписание читает один) и не в
точке входа (там его не прочитать, не подняв окружение целиком). Признак
границы — число потребителей, а не сходство содержимого.

Имя задачи берётся из функции, а не пишется строкой: строка разошлась бы с
реестром молча.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from taskiq.scheduler.scheduled_task import ScheduledTask

from app.interface.worker.handlers import users

_HOURLY = "0 * * * *"


@dataclass(frozen=True)
class Scheduled:
    handler: Callable[..., Any]
    cron: str


SCHEDULE: list[Scheduled] = [
    Scheduled(users.purge_inactive_users, _HOURLY),
]


class Schedule:
    @staticmethod
    def scheduled_tasks() -> list[ScheduledTask]:
        return [
            ScheduledTask(
                task_name=item.handler.__name__,
                labels={},
                args=[],
                kwargs={},
                cron=item.cron,
                # ВЫВЕДЕН из записи, а не `uuid4()`: объекты создаются заново
                # на каждый опрос, и на случайном ключе умирают обе защиты
                # taskiq — «в этой минуте уже отправляли» и «предыдущая ещё
                # идёт». С `--interval` меньше минуты это дубль на каждый опрос.
                schedule_id=f"{item.handler.__name__}:{item.cron}",
            )
            for item in SCHEDULE
        ]
