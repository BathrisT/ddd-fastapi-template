from dataclasses import dataclass

from app.domain.events.base import DomainEvent


@dataclass(frozen=True)
class UserRegistered(DomainEvent):
    """Пользователь заведён и зафиксирован в базе.

    Событие несёт идентификатор, а не саму сущность: обработчик прочитает её
    сам, уже своей сессией. Класть в событие объект значило бы отдать в другую
    транзакцию снимок, который к моменту обработки успел устареть.
    """

    user_id: int
