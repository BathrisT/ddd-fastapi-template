"""Сценарий по расписанию: убрать неподтверждённые регистрации.

Момент считается здесь: «что считать протухшим» — правило приложения, одно для
расписания и для запуска руками. Условие «только неактивные» принадлежит
запросу, а не вызывающему.

ВНИМАНИЕ, ЗАВОДЯ ПРОЕКТ. Активным в демо не становится никто — ручки
подтверждения нет, — значит уборка снесёт КАЖДОГО, кто завёлся через
`POST /users`. Пойдут настоящие люди — либо появляется подтверждение, либо
строка убирается из расписания. Третьего нет, и обнаружится это через месяц.
"""

from datetime import UTC, datetime, timedelta

from loguru import logger

from app.application.ports.committer import Committer
from app.application.ports.repositories.user_repo import UserRepo


class PurgeInactiveUsersUseCase:
    def __init__(self, users_repo: UserRepo, committer: Committer) -> None:
        self._users_repo = users_repo
        self._committer = committer

    async def execute(self, keep_days: int) -> int:
        cutoff = datetime.now(tz=UTC) - timedelta(days=keep_days)
        removed = await self._users_repo.delete_inactive_before(cutoff)
        await self._committer.commit()
        if removed:
            logger.info("purge_inactive_users: удалено {} регистраций старше {}", removed, cutoff)
        return removed
