from dataclasses import dataclass

from app.domain.events.base import DomainEvent


@dataclass(frozen=True)
class UserRegistered(DomainEvent):
    """Пользователь заведён и зафиксирован в базе.

    Несёт идентификатор, а не сущность: снимок к моменту обработки устареет,
    а обработчик прочитает её своей сессией.
    """

    user_id: int
