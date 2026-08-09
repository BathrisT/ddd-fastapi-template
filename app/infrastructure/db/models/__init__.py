"""Реестр ORM-моделей.

Здесь обязана быть названа КАЖДАЯ модель: `migrations/env.py` импортирует
только этот модуль, и таблица, о которой он не знает, для автогенерации
миграций не существует — `alembic revision --autogenerate` молча предложит её
удалить, а `make schema-check` этого не поймает, потому что сверяет с тем же
неполным `Base.metadata`.
"""

from app.infrastructure.db.models.base import Base, CreatedAtMixin, TimestampMixin
from app.infrastructure.db.models.user import UserORM

__all__ = ["Base", "CreatedAtMixin", "TimestampMixin", "UserORM"]
