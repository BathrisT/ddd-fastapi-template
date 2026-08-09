import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.models.base import Base, TimestampMixin


class UserORM(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        # Уникальность почты — здесь, а не только проверкой в сценарии: между
        # проверкой и вставкой успевает влезть параллельный запрос, и без
        # индекса в базе оказались бы двое с одной почтой.
        sa.UniqueConstraint("email", name="uq_users_email"),
        # Под уборку неподтверждённых: без индекса она читает таблицу целиком,
        # и чем дольше живёт проект, тем дороже становится задача, которая по
        # смыслу должна дешеветь.
        sa.Index("ix_users_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    welcome_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
