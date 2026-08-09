"""Доменное событие → задача очереди.

Место здесь, а не во входе: маршрутизатор знает и домен, и имена задач, а
дёргает его публикатор, работающий во ВСЕХ входах — и в API, и в воркере.
Признак композиции — число потребителей, а не сходство содержимого: расписание
тоже ссылается на реестр, но читает его один шедулер, поэтому живёт в своей
точке входа.

Ставит по имени обработчика — `handler.__name__`, то есть ссылку на объект:
опечатку ловит импорт, а не рантайм. Локальные импорты с `noqa` здесь означали
бы, что зависимость идёт не туда — маршрутизатор лежит не в том слое.
"""

from loguru import logger

from app.application.ports.task_queue import TaskQueue
from app.domain.events.base import DomainEvent
from app.domain.events.user_registered import UserRegistered
from app.interface.worker.handlers import users


class EventRouter:
    """Реализация порта `EventPublisher`: публикация = постановка нужной задачи."""

    def __init__(self, queue: TaskQueue) -> None:
        self._queue = queue

    async def publish(self, event: DomainEvent) -> None:
        if isinstance(event, UserRegistered):
            await self._queue.enqueue(users.welcome_user.__name__, user_id=event.user_id)
            return

        # Событие без ветки — забытая маршрутизация, и молчать об этом нельзя:
        # сценарий отработает, `commit` пройдёт, задача не появится, и отказ
        # будет выглядеть как «регистрация есть, приветствия нет». Это последний
        # тихий шов флоу — постановку по незнакомому имени `TaskQueue.enqueue`
        # уже роняет, реестр стережёт `check_entrypoint_registry.py`.
        #
        # Запись в лог, а не исключение: публикуют ПОСЛЕ `commit()` (см.
        # `RegisterUserUseCase`), и брошенное отсюда исключение превратило бы
        # уже зафиксированный сценарий в 500 — задача от этого всё равно не
        # появится, а клиент получит отказ на успешно выполненной работе.
        logger.error(
            "event_router: событие {} никуда не маршрутизировано — "
            "нужна ветка в EventRouter.publish",
            type(event).__name__,
        )
