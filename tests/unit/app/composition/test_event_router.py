"""Маршрутизация доменного события в задачу очереди.

Отдельный тест ради второй ветки: событие, для которого маршрут забыли,
не должно исчезать молча. Тихое исчезновение здесь неотличимо от нормы —
сценарий отработал, `commit` прошёл, наружу это выглядит как «регистрация
есть, а приветствия нет», и искать причину пойдут в воркер, где её нет.
"""

from dataclasses import dataclass

from loguru import logger

from app.composition.event_router import EventRouter
from app.domain.events.base import DomainEvent
from app.domain.events.user_registered import UserRegistered


@dataclass(frozen=True)
class _Unrouted(DomainEvent):
    """Событие, для которого ветку в `publish` не завели."""


class FakeTaskQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def enqueue(self, task_name: str, **kwargs: object) -> str:
        self.calls.append((task_name, kwargs))
        return "job-1"

    async def get(self, job_id: str) -> object:
        raise NotImplementedError("маршрутизатору исход задачи не нужен")


async def test_user_registered_becomes_a_welcome_task():
    queue = FakeTaskQueue()

    await EventRouter(queue).publish(UserRegistered(user_id=7))

    # Имя задачи — имя функции-обработчика, и здесь оно проверяется целиком:
    # переименуй `welcome_user` — и уже поставленные сообщения свою задачу не
    # найдут (см. `composition/worker_tasks.py`).
    assert queue.calls == [("welcome_user", {"user_id": 7})]


async def test_unrouted_event_does_not_vanish_silently():
    queue = FakeTaskQueue()
    said: list[str] = []
    sink = logger.add(said.append, level="ERROR")
    try:
        await EventRouter(queue).publish(_Unrouted())
    finally:
        logger.remove(sink)

    assert queue.calls == []
    assert any("_Unrouted" in line for line in said)
