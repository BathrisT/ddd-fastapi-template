"""Сценарий: завести пользователя.

Событие публикуется ПОСЛЕ `commit()`, и это не стилистика. Публикация ставит
задачу в очередь, воркер разбирает её мгновенно и своей сессией — опубликуй мы
до фиксации, задача прочитала бы базу раньше, чем в ней появится строка, и
упала бы «не найдено» на том, что вот-вот появится.

**Цена этого порядка названа вслух: между `commit()` и `publish()` нет
восстановления.** Упади постановка (Redis недоступен, процесс убит ровно
здесь) — пользователь уже в базе, а задача не поставлена никогда; клиент
получит 500, повторит запрос и получит 409, потому что пользователь есть.
Приветствия у него не будет, и подобрать это некому. В демо цена — пустое
поле; в проекте, где на этом месте письмо о регистрации, — неотправленное
письмо. Лечится это не перестановкой строк (обратный порядок ломает чтение
воркером), а outbox'ом: событие пишется в ту же транзакцию, отдельный
разборщик ставит задачи. Заводить его в шаблоне не стали — он тянет за собой
свою таблицу, своего разборщика и своё расписание, а нужен не всякому проекту.
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

        # Проверка ради внятного отказа, а не ради корректности: между ней и
        # вставкой успевает влезть параллельный запрос. Настоящий сторож —
        # уникальный индекс в базе, и репозиторий переводит его отказ в тот же
        # `ConflictError`. Без проверки пользователь получал бы 409 только на
        # гонке, а на обычном повторе — тоже 409, но из глубины адаптера.
        if await self._users_repo.get_by_email(user.email) is not None:
            raise ConflictError(f"Пользователь с почтой {user.email} уже есть")

        # Работаем с ВОЗВРАЩЁННЫМ объектом, а не с переданным: идентификатор
        # присваивает база, и у аргумента он так и остаётся нулевым. Событие с
        # `user_id=0` ничего бы не сломало на месте — оно просто ушло бы в
        # никуда, а обработчик написал бы «пользователя 0 больше нет».
        saved = await self._users_repo.save(user)
        await self._committer.commit()

        await self._events.publish(UserRegistered(user_id=saved.id))
        return saved
