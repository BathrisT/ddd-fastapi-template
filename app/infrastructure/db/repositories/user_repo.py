"""Реализация порта пользователей поверх SQLAlchemy.

Сессия приходит снаружи и закрывается вместе со входом — репозиторий её не
создаёт и не коммитит (CLAUDE.md, «Транзакции»). Здесь только `flush()`: он
отдаёт присвоенный базой идентификатор, не решая за сценарий, где у того
граница транзакции.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exceptions import ConflictError, NotFoundError
from app.domain.models.user import User
from app.infrastructure.db.models.user import UserORM


class SqlUserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, user: User) -> User:
        # Развилка «вставить или обновить» живёт ЗДЕСЬ и только здесь: это
        # единственное место, знающее, что `0` значит «ещё не сохранён».
        # Разъедься она по сценариям — и каждый начал бы решать по-своему.
        if user.id == 0:
            orm = UserORM(
                email=user.email,
                name=user.name,
                is_active=user.is_active,
                welcome_message=user.welcome_message,
            )
            self._session.add(orm)
        else:
            found = await self._session.get(UserORM, user.id)
            if found is None:
                raise NotFoundError(f"Пользователь {user.id} не найден")
            orm = found
            orm.email = user.email
            orm.name = user.name
            orm.is_active = user.is_active
            orm.welcome_message = user.welcome_message
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Отказ уникального индекса переводится в доменный здесь и только
            # здесь: `IntegrityError` — это знание о базе, и уехав в сценарий,
            # оно потянуло бы туда же имя констрейнта.
            #
            # Откат НЕ делаем: сессией владеет контейнер, и `async with`
            # привходового скоупа закроет её сам, отбросив незакоммиченное.
            # Свой `rollback()` здесь отменил бы заодно всё, что сценарий успел
            # сделать до этого, — то есть адаптер решил бы за него судьбу чужой
            # транзакции.
            raise ConflictError(f"Пользователь с почтой {user.email} уже есть") from exc
        await self._session.refresh(orm)
        return self._to_domain(orm)

    async def get_by_id(self, user_id: int) -> User | None:
        orm = await self._session.get(UserORM, user_id)
        return self._to_domain(orm) if orm else None

    async def get_by_email(self, email: str) -> User | None:
        stmt = sa.select(UserORM).where(UserORM.email == email)
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_domain(orm) if orm else None

    async def list_recent(self, limit: int) -> list[User]:
        # Второй ключ сортировки обязателен: `created_at` ставит база значением
        # `now()`, а это время НАЧАЛА ТРАНЗАКЦИИ — у строк, заведённых одним
        # запросом, оно одинаковое до микросекунды. Без тай-брейка порядок
        # выдачи не определён, и пагинация начинает терять и повторять строки.
        stmt = (
            sa.select(UserORM).order_by(UserORM.created_at.desc(), UserORM.id.desc()).limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_domain(orm) for orm in rows]

    async def delete_inactive_before(self, moment: datetime) -> int:
        stmt = sa.delete(UserORM).where(
            UserORM.is_active.is_(False),
            UserORM.created_at < moment,
        )
        result = await self._session.execute(stmt)
        # `execute` типизирован как `Result`, у которого счётчика строк нет — он
        # появляется только у `CursorResult`, который DELETE и возвращает.
        # Сужение типом, а не `cast`: если однажды сюда приедет не курсор,
        # проверка увидит это, а `cast` промолчал бы.
        return result.rowcount if isinstance(result, CursorResult) else 0

    def _to_domain(self, orm: UserORM) -> User:
        return User(
            id=orm.id,
            email=orm.email,
            name=orm.name,
            is_active=orm.is_active,
            created_at=orm.created_at,
            welcome_message=orm.welcome_message,
        )
