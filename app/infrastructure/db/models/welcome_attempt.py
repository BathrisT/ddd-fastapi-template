import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.models.base import Base, CreatedAtMixin


class WelcomeAttemptORM(Base, CreatedAtMixin):
    """След обращения к языковой модели, переживающий откат сценария.

    Внешнего ключа на `users` здесь НЕТ намеренно, хотя связь по смыслу есть.
    Запись автономная: она фиксируется своей транзакцией и обязана уцелеть,
    даже если сценарий откатился, а пользователь так и не появился. Внешний
    ключ означал бы отказ вставки ровно в том случае, ради которого журнал и
    заведён.
    """

    __tablename__ = "welcome_attempts"
    __table_args__ = (
        # Читают журнал по пользователю: «сколько раз мы уже платили за него».
        sa.Index("ix_welcome_attempts_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
