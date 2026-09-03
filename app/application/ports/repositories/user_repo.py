"""Порт хранилища пользователей.

Возвращает доменные модели, а не ORM-объекты (золотое правило 4): иначе
знание о колонках и о том, что объект привязан к живой сессии, расползается по
сценариям, и первый же `expire_on_commit` превращается в загадочный запрос из
середины бизнес-логики.
"""

from datetime import datetime
from typing import Protocol

from app.domain.models.user import User


class UserRepo(Protocol):
    async def save(self, user: User) -> User:
        """Завести нового или обновить существующего. Коммитит вызывающий.

        `id == 0` — вставка, иначе обновление; дальше работают с ВОЗВРАЩЁННЫМ
        объектом. Занятую почту поднимает `ConflictError`. Обновление пишет
        строку ЦЕЛИКОМ: со вторым писателем оно тихо отменит чужую правку.
        """
        ...

    async def get_by_id(self, user_id: int) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...

    async def list_recent(self, limit: int) -> list[User]: ...

    async def delete_inactive_before(self, moment: datetime) -> int:
        """Удалить НЕАКТИВНЫХ, заведённых раньше момента. Возвращает число строк."""
        ...
