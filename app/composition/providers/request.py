"""Привходовой скоуп: то, что живёт один запрос, одну задачу, один апдейт.

Сессия создаётся здесь, а не на границе каждого входа. Правило «один `commit()`
в конце сценария» от этого не меняется: `Committer` — порт над сессией, ему
безразлично, кто её создал. Меняется только момент закрытия — выход из
привходового скоупа вместо ручного `async with` в каждом обработчике.

Откат по умолчанию сохраняется: `async with` фабрики закрывает сессию, и
незакоммиченное отбрасывается.

Правило целиком — docs/rules/композиция-и-скоупы.md
"""

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.committer import Committer
from app.infrastructure.db.committer import SqlCommitter


class RequestProvider(Provider):
    scope = Scope.REQUEST

    @provide
    async def session(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    @provide
    def committer(self, session: AsyncSession) -> Committer:
        return SqlCommitter(session)
