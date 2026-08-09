"""Короткая транзакция вне сессии сценария — единственный фасад автономной записи.

Есть записи, которые обязаны пережить судьбу вызывающего: журнал доставки
(сообщение уже ушло наружу — откатить этот факт нельзя) и идентификатор,
выданный внешней системой (он обязан сохраниться, иначе следующий вызов заведёт
второй такой же). Обе случаются из сервиса, а не из сценария, и как до
финального `commit()`, так и после него.

Писать это в сессию вызывающего нельзя в обе стороны. После его `commit()`
запись останется незакоммиченной и умрёт вместе с закрытием сессии. А
собственный `commit()` в чужой сессии зафиксирует ЗАОДНО всё, что сценарий
успел изменить и ещё не собирался фиксировать, — то есть адаптер решит за
сценарий, где у того граница транзакции.

Engine — отдельный (`AutonomousEngine`, NullPool): второе соединение из
основного пула на всплеске уходит в ожидание за теми, кто его и держит.

Для НОВОЙ автономной записи это единственная дверь, и охраняет её
`scripts/check_db_access.py`: пока «своя сессия» открывается по месту, каждое
новое место заводит собственные правила жизни транзакции.
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
