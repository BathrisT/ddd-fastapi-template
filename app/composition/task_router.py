"""Намерение → обработчик. Один шов на все задачи, а не класс на каждую.

Симметричен `EventRouter` и живёт здесь по той же причине: связать намерение из
`application` с обработчиком из `interface` может только тот, кому позволено
видеть оба слоя, а это `composition`.

**Отличие от маршрутизатора событий — в поведении на незнакомом.** Тот пишет в
лог и продолжает: событие никто не ждёт, и падение сценария после `commit()`
превратило бы уже сделанную работу в 500. Здесь наоборот: вызывающему обещан
номер задачи, и вернуть ему выдуманный — значит отправить его опрашивать
исход, которого не будет никогда. Поэтому отказ, и на месте постановки.

Имя задачи — `handler.__name__`, ссылка на объект: опечатку ловит импорт.
"""

from app.application.dto.tasks import TaskIntent, WelcomeUser
from app.application.ports.task_queue import TaskQueue
from app.interface.worker.handlers import users


class TaskRouter:
    """Реализация порта `TaskSubmitter`: отдельной обёртки нет, как и у событий."""

    def __init__(self, queue: TaskQueue) -> None:
        self._queue = queue

    async def submit(self, intent: TaskIntent) -> str:
        if isinstance(intent, WelcomeUser):
            return await self._queue.enqueue(users.welcome_user.__name__, user_id=intent.user_id)

        raise NotImplementedError(
            f"Для намерения {type(intent).__name__} нет ветки в TaskRouter.submit"
        )
