"""welcome_attempts

Revision ID: 0002_welcome_attempts
Revises: 0001_users
Create Date: 2026-08-10
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_welcome_attempts"
down_revision = "0001_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "welcome_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Внешнего ключа на `users` нет намеренно: запись автономная и обязана
    # уцелеть даже там, где сценарий откатился и пользователя не появилось.
    op.create_index("ix_welcome_attempts_user_id", "welcome_attempts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_welcome_attempts_user_id", table_name="welcome_attempts")
    op.drop_table("welcome_attempts")
