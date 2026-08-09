"""Публикатор-заглушка для тестов: событий в Taskiq не создаёт."""

from app.domain.events.base import DomainEvent


class NoopEventPublisher:
    """Does nothing — used in tests."""

    async def publish(self, event: DomainEvent) -> None:
        pass
