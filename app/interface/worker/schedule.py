"""Расписание: что и как часто дёргать.

Не в композиции — композицию импортируют ВСЕ входы, а расписание читает ровно
один. Но и не в самой точке входа: там его нельзя ни прочитать, ни проверить,
не подняв окружение целиком (точка входа читает настройки на импорте), а
таблица «что запускается по времени» — первое, на что смотрят при разборе
«почему не пришло».

Сравнение с соседом делает границу измеримой: маршрутизатор событий тоже
ссылается на входы, но его дёргает публикатор, работающий во всех входах, —
поэтому он в композиции. Признак — число потребителей, а не сходство
содержимого.

Имя задачи берётся из самой функции, а не пишется строкой: строка разошлась бы
с реестром молча, а `handler.__name__` — ссылка на объект, и опечатку ловит
импорт. Что каждое имя отсюда зарегистрировано, проверяет
`tests/unit/app/composition/test_worker_tasks.py`.
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
            )
            for item in SCHEDULE
        ]
