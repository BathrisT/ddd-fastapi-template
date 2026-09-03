"""Реестр задач очереди — единственный список входов очереди.

Регистрация явная, а не побочный эффект импорта: списки import-строк по точкам
входа расходятся молча, и забытая строка даёт не ошибку сборки, а отказ в проде.
Имя задачи — имя функции-обработчика.

РЕТРАЕВ НЕТ, и это решение: повторять можно только идемпотентную работу, а
отправка наружу и платное обращение к модели повтора не переживут. Включать
`SimpleRetryMiddleware` можно лишь после того, как каждая задача станет
идемпотентной поимённо. И помни: имя задачи едет в Redis внутри уже
поставленных сообщений — переименование теряет их на деплое.
"""

from collections.abc import Callable
from typing import Any, ClassVar

from dishka.integrations.taskiq import inject
from taskiq import AsyncBroker

from app.interface.worker.handlers import users


class WorkerTasks:
    TABLE: ClassVar[list[Callable[..., Any]]] = [
        # ── По событию или по требованию ──────────────────────────
        users.welcome_user,
        # ── По расписанию ─────────────────────────────────────────
        users.purge_inactive_users,
    ]

    @staticmethod
    def register(broker: AsyncBroker) -> None:
        """Повесить обработчики на брокер. Зовётся каждой точкой входа очереди."""
        for handler in WorkerTasks.TABLE:
            broker.register_task(inject(handler, patch_module=True), task_name=handler.__name__)
