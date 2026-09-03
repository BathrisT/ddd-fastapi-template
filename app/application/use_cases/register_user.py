"""Сценарий: завести пользователя.

Событие публикуется ПОСЛЕ `commit()`: воркер разбирает очередь мгновенно и
своей сессией, а до фиксации прочитал бы базу раньше, чем в ней появится
строка. Цена порядка — между `commit()` и `publish()` нет восстановления:
упади постановка, пользователь останется в базе без приветствия навсегда.
Лечится outbox'ом, а не перестановкой строк; в шаблон не заведён.
"""

from dataclasses import dataclass

from app.application.ports.committer import Committer
from app.application.ports.event_publisher import EventPublisher
from app.application.ports.repositories.user_repo import UserRepo
from app.domain.events.user_registered import UserRegistered
from app.domain.exceptions import ConflictError
from app.domain.models.user import User


@dataclass(frozen=True)
class RegisterUserCommand:
    email: str
    name: str


class RegisterUserUseCase:
    def __init__(self, users_repo: UserRepo, committer: Committer, events: EventPublisher) -> None:
        self._users_repo = users_repo
        self._committer = committer
        self._events = events

    async def execute(self, command: RegisterUserCommand) -> User:
        # Правила заведения — у сущности: они обязаны действовать на любом
        # входе, а не только в этом сценарии. Сценарию остаётся оркестровка.
        user = User.register(command.email, command.name)

        # Ради внятного отказа, а не корректности: между проверкой и вставкой
        # влезает параллельный запрос, а сторож — уникальный индекс.
        if await self._users_repo.get_by_email(user.email) is not None:
            raise ConflictError(f"Пользователь с почтой {user.email} уже есть")

        # Дальше — с ВОЗВРАЩЁННЫМ объектом: идентификатор присваивает база, у
        # аргумента он нулевой. Событие с `user_id=0` уходит в никуда молча.
        saved = await self._users_repo.save(user)
        await self._committer.commit()

        await self._events.publish(UserRegistered(user_id=saved.id))
        return saved
