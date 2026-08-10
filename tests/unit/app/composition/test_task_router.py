"""Намерение → задача, и громкий отказ на незнакомом.

Парный к `test_event_router.py`, и проверяет ровно то, чем два маршрутизатора
отличаются: событие без ветки уходит в лог и не роняет сценарий, а намерение
без ветки обязано упасть на месте постановки. Вернуть выдуманный номер значило
бы отправить вызывающего опрашивать исход, которого не будет никогда.
"""

from dataclasses import dataclass

import pytest

from app.application.dto.tasks import TaskIntent, WelcomeUser
from app.composition.task_router import TaskRouter


@dataclass(frozen=True)
class _Unrouted(TaskIntent):
    """Намерение, для которого ветку в `submit` не завели."""


class FakeTaskQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def enqueue(self, task_name: str, **kwargs: object) -> str:
        self.calls.append((task_name, kwargs))
        return "job-1"

    async def get(self, job_id: str) -> object:
        raise NotImplementedError


async def test_known_intent_becomes_its_task() -> None:
    queue = FakeTaskQueue()

    job_id = await TaskRouter(queue).submit(WelcomeUser(user_id=7))

    assert job_id == "job-1"
    assert queue.calls == [("welcome_user", {"user_id": 7})]


async def test_unknown_intent_refuses_instead_of_lying() -> None:
    queue = FakeTaskQueue()

    with pytest.raises(NotImplementedError, match="_Unrouted"):
        await TaskRouter(queue).submit(_Unrouted())

    assert queue.calls == []
