from dataclasses import dataclass


@dataclass(frozen=True)
class DomainEvent:
    """Base class for all domain events."""
