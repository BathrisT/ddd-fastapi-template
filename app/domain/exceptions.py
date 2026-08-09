"""Доменные отказы. Каждый отвечает на «что пошло не так по существу», а не
«каким кодом это отдать наружу» — перевод в HTTP-код живёт в слое входа
(`interface/api/exception_handlers.py`), и он же единственный, кто про коды
знает.
"""


class DomainError(Exception):
    """Base domain exception."""


class NotFoundError(DomainError):
    """Entity not found."""


class ValidationError(DomainError):
    """Domain validation error."""


class ConflictError(DomainError):
    """Entity already exists or state conflict."""


class ForbiddenError(DomainError):
    """Action not permitted."""


class AuthError(DomainError):
    """Authentication or verification failed."""
