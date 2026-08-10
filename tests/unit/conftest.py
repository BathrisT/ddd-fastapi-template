"""Общие дублёры unit-тестов.

Дублёр порта — обычный класс, а не `MagicMock`: мок принимает любой вызов, в
том числе тот, которого в порту уже нет, и тест остаётся зелёным после
переименования метода. Здесь же переименование ломает дублёр, то есть
компиляцию теста, — ровно тогда, когда должно.

Живут в общем conftest, а не копией в каждом файле: десять почти одинаковых
заглушек разъезжаются на первом же изменении порта.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.events.base import DomainEvent
from app.domain.exceptions import ConflictError, NotFoundError
from app.domain.models.user import User


class FakeUserRepo:
    """Хранилище в памяти. Уникальность почты стережёт так же, как база.

    Наружу и внутрь всегда уходит КОПИЯ, и это не аккуратность ради
    аккуратности. Отдавай дублёр тот же объект, что ему передали, — и главный
    инвариант раздела «Идентификаторы» (`save` присваивает id базой, дальше
    работают с ВОЗВРАЩЁННЫМ объектом) стал бы в unit-тестах непроверяемым:
    сценарий, публикующий событие с `command.id` вместо `saved.id`, остался бы
    зелёным, а в проде ушёл бы с `user_id=0`. Копия ломает такой сценарий здесь,
    где это стоит одного упавшего теста.

    Единственная сознательная поблажка против SQL-репозитория: `created_at`
    хранится как передали, тогда как в базе его ставит `server_default`. Иначе
    unit-тесту нечем состарить строку — интеграционный вместо этого двигает
    границу отсечки.
    """

    def __init__(self, users: list[User] | None = None) -> None:
        self.rows: dict[int, User] = {}
        self._next_id = 1
        for user in users or []:
            self._put(user)

    def _put(self, user: User) -> User:
        stored = replace(user)
        if stored.id == 0:
            stored.id = self._next_id
            self._next_id += 1
        if stored.created_at is None:
            stored.created_at = datetime.now(tz=UTC)
        self.rows[stored.id] = stored
        return replace(stored)

    async def save(self, user: User) -> User:
        clash = await self.get_by_email(user.email)
        if clash is not None and clash.id != user.id:
            raise ConflictError(f"Пользователь с почтой {user.email} уже есть")
        # Как в `SqlUserRepo.save`: непустой id, которого нет в хранилище, —
        # это обновление несуществующей строки, а не тихая вставка под чужим
        # номером.
        if user.id != 0 and user.id not in self.rows:
            raise NotFoundError(f"Пользователь {user.id} не найден")
        return self._put(user)

    async def get_by_id(self, user_id: int) -> User | None:
        row = self.rows.get(user_id)
        return replace(row) if row else None

    async def get_by_email(self, email: str) -> User | None:
        row = next((u for u in self.rows.values() if u.email == email), None)
        return replace(row) if row else None

    async def list_recent(self, limit: int) -> list[User]:
        # Тай-брейк по id — как в SQL-репозитории: `created_at` там ставит база
        # временем начала транзакции, и у соседних строк оно совпадает.
        ordered = sorted(self.rows.values(), key=lambda u: (u.created_at, u.id), reverse=True)
        return [replace(row) for row in ordered[:limit]]

    async def delete_inactive_before(self, moment: datetime) -> int:
        doomed = [
            user_id
            for user_id, user in self.rows.items()
            if not user.is_active and user.created_at is not None and user.created_at < moment
        ]
        for user_id in doomed:
            del self.rows[user_id]
        return len(doomed)


class FakeCommitter:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class RecordingPublisher:
    """Складывает события вместо постановки задач — как `NoopEventPublisher`,
    но помнит, что именно опубликовали."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)


class FakeAi:
    def __init__(self, answer: str = "Привет!") -> None:
        self.answer = answer
        self.calls: list[str] = []

    async def welcome_text(self, name: str) -> str:
        self.calls.append(name)
        return self.answer


@pytest.fixture
def users() -> FakeUserRepo:
    return FakeUserRepo()


@pytest.fixture
def committer() -> FakeCommitter:
    return FakeCommitter()


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def ai() -> FakeAi:
    return FakeAi()
