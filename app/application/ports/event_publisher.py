from typing import Protocol

from app.domain.events.base import DomainEvent


class EventPublisher(Protocol):
    async def publish(self, event: DomainEvent) -> None: ...
