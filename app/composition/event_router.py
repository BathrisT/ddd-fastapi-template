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

from app.application.ports.event_publisher import EventPublisher
from app.application.ports.task_queue import TaskQueue
from app.domain.events.base import DomainEvent
from app.domain.events.user_registered import UserRegistered
from app.interface.worker.handlers import users


class EventRouter(EventPublisher):
    """Реализация порта `EventPublisher`: публикация = постановка нужной задачи."""

    def __init__(self, queue: TaskQueue) -> None:
        self._queue = queue

    async def publish(self, event: DomainEvent) -> None:
        if isinstance(event, UserRegistered):
            await self._queue.enqueue(users.welcome_user.__name__, user_id=event.user_id)
            return

        # Забытая маршрутизация выглядит как «регистрация есть, приветствия
        # нет»: сценарий отработал, задача не появилась. Лог, а не исключение —
        # публикуют ПОСЛЕ `commit()`, и отсюда оно превратило бы уже
        # зафиксированную работу в 500, не создав задачи.
        logger.error(
            "event_router: событие {} никуда не маршрутизировано — "
            "нужна ветка в EventRouter.publish",
            type(event).__name__,
        )
