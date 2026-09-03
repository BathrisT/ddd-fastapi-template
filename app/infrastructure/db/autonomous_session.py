"""Короткая транзакция вне сессии сценария — фасад автономной записи.

Есть записи, обязанные пережить судьбу вызывающего: журнал доставки и выданный
извне идентификатор. В чужую сессию их нельзя в обе стороны — после её
`commit()` запись умрёт незакоммиченной, а свой `commit()` там зафиксирует
заодно всё, что сценарий фиксировать не собирался.

Engine отдельный (NullPool): второе соединение из основного пула на всплеске
ждало бы тех, кто его и держит. Для новой автономной записи это единственная
дверь, охраняет `scripts/check_db_access.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.infrastructure.db.autonomous_engine import AutonomousEngine


class AutonomousSession:
    def __init__(self, main_engine: AsyncEngine) -> None:
        self._bind = AutonomousEngine.for_(main_engine)

    @asynccontextmanager
    async def open(self) -> AsyncIterator[AsyncSession]:
        """Сессия на одну короткую операцию. Коммитит вызывающий репозиторий."""
        async with AsyncSession(bind=self._bind, expire_on_commit=False) as session:
            yield session
