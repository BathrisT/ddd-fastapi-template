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
                # ВЫВЕДЕН из записи, а не сгенерирован. По умолчанию у
                # `ScheduledTask` это `uuid4()`, а объекты здесь создаются
                # заново на каждый опрос шедулера — то есть идентификатор был
                # бы каждый раз новым. Между тем цикл taskiq держит по нему две
                # защиты: «эту крон-задачу в текущей минуте уже отправляли» и
                # «предыдущая отправка ещё идёт». На случайном ключе обе мертвы.
                #
                # При штатном опросе (раз в минуту) это незаметно: совпадение
                # одно, дедуплицировать нечего. Но `taskiq scheduler --interval`
                # меньше минуты превращает это в детерминированный дубль —
                # каждый опрос внутри совпавшей минуты отправит задачу заново.
                # То же, если один проход цикла займёт больше минуты.
                schedule_id=f"{item.handler.__name__}:{item.cron}",
            )
            for item in SCHEDULE
        ]
