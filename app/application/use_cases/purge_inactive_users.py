"""Сценарий по расписанию: убрать неподтверждённые регистрации.

Момент считается здесь, а не в обработчике очереди: «что считать протухшей
регистрацией» — правило приложения, и оно обязано быть одним для запуска по
расписанию и для запуска руками. В обработчике осталось бы только распаковать
аргументы — и второй вызывающий неизбежно посчитал бы срок по-своему.

Удаляются ТОЛЬКО неактивные, и это условие принадлежит запросу, а не
вызывающему: сценарий, который умеет удалить активного при неверном аргументе,
рано или поздно его удалит.

**Внимание, заводя проект из шаблона.** Активным пользователь в демо не
становится нигде: ручки подтверждения почты в шаблоне нет, `is_active` так и
остаётся `False`. Значит эта уборка через `keep_days` дней безвозвратно снесёт
КАЖДОГО, кто завёлся через `POST /users`. Пока это демонстрация шва «расписание
→ замок → сценарий», всё честно; как только через ручку пойдут настоящие люди —
либо появляется сценарий подтверждения, ставящий `is_active=True`, либо строка
`purge_inactive_users` убирается из расписания. Третьего варианта нет, и
обнаружится он через месяц после запуска.
"""

from datetime import UTC, datetime, timedelta

from loguru import logger

from app.application.ports.committer import Committer
from app.application.ports.repositories.user_repo import UserRepo


class PurgeInactiveUsersUseCase:
    def __init__(self, users: UserRepo, committer: Committer) -> None:
        self._users = users
        self._committer = committer

    async def execute(self, keep_days: int) -> int:
        cutoff = datetime.now(tz=UTC) - timedelta(days=keep_days)
        removed = await self._users.delete_inactive_before(cutoff)
        await self._committer.commit()
        if removed:
            logger.info("purge_inactive_users: удалено {} регистраций старше {}", removed, cutoff)
        return removed
