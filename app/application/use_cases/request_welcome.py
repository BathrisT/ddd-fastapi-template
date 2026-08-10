"""Сценарий: поставить приветствие в очередь по требованию и вернуть номер задачи.

Отличается от регистрации тем, что исход задачи здесь ЖДУТ: клиент получает
идентификатор и опрашивает его, пока крутится прогресс. Ровно для этого случая
заведён порт `JobResults` — задача, поставленная событием (как приветствие
после регистрации), исхода не опрашивает, её никто не ждёт.

Проверка существования пользователя здесь не ради корректности — обработчик
всё равно переспросит базу своей сессией, — а ради внятного отказа: 404 сразу,
а не «задача поставлена» и тишина в ответ.

`commit()` тут нет, и это не упущение: сценарий ничего не пишет.
"""

from app.application.dto.tasks import WelcomeUser
from app.application.ports.repositories.user_repo import UserRepo
from app.application.ports.task_submitter import TaskSubmitter
from app.domain.exceptions import NotFoundError


class RequestWelcomeUseCase:
    def __init__(self, users: UserRepo, tasks: TaskSubmitter) -> None:
        self._users = users
        self._tasks = tasks

    async def execute(self, user_id: int) -> str:
        if await self._users.get_by_id(user_id) is None:
            raise NotFoundError(f"Пользователь {user_id} не найден")

        return await self._tasks.submit(WelcomeUser(user_id=user_id))
