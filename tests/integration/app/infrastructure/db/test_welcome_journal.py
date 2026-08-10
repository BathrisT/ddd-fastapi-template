"""Автономная запись переживает откат вызывающего — ради этого она и заведена.

Тест ровно про то, чего нельзя проверить на дублёрах: сессия сценария и сессия
журнала — разные, у них разные транзакции и разные соединения. Замени журнал
дублёром — и утверждение станет бессмысленным, потому что откатывать будет
нечего.
"""

from sqlalchemy import text

from app.infrastructure.db.autonomous_session import AutonomousSession
from app.infrastructure.db.repositories.welcome_journal import SqlWelcomeJournal


async def _attempts(session, user_id: int) -> list[str]:
    rows = await session.execute(
        text("SELECT outcome FROM welcome_attempts WHERE user_id = :uid"), {"uid": user_id}
    )
    return [row[0] for row in rows]


async def test_record_survives_caller_rollback(engine, session):
    """Сценарий откатился — след обращения к модели остался.

    Иначе следующий прогон обратился бы к платной модели снова, не зная, что
    за эту попытку уже заплачено.
    """
    journal = SqlWelcomeJournal(AutonomousSession(engine))

    # Пишем в сессию вызывающего что-то своё и НЕ коммитим — как повёл бы себя
    # сценарий, упавший после обращения к модели.
    await session.execute(
        text("INSERT INTO users (email, name, is_active) VALUES ('rolled@back', 'Откат', false)")
    )
    await journal.record(user_id=777, outcome="success")
    await session.rollback()

    assert await _attempts(session, 777) == ["success"]
    remaining = await session.execute(
        text("SELECT count(*) FROM users WHERE email = 'rolled@back'")
    )
    # Работа сценария откатилась целиком, а запись журнала — нет.
    assert remaining.scalar() == 0


async def test_record_does_not_commit_the_callers_work(engine, session):
    """Журнал не фиксирует ЗАОДНО то, что сценарий фиксировать не собирался.

    Обратная сторона той же границы: своя транзакция не только выживает
    отдельно, но и не утаскивает за собой чужое незавершённое.
    """
    journal = SqlWelcomeJournal(AutonomousSession(engine))

    await session.execute(
        text("INSERT INTO users (email, name, is_active) VALUES ('pending@work', 'Ждёт', false)")
    )
    await journal.record(user_id=778, outcome="error")
    await session.rollback()

    left = await session.execute(text("SELECT count(*) FROM users WHERE email = 'pending@work'"))
    assert left.scalar() == 0
    assert await _attempts(session, 778) == ["error"]
