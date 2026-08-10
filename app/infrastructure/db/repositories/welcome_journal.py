"""Запись в журнал попыток — своей транзакцией, а не сессией сценария.

Единственный в шаблоне потребитель `AutonomousSession`, и он же образец: любая
новая запись, обязанная пережить откат вызывающего, заводится ровно так.

Почему не сессия сценария — обе стороны плохи. Записать в неё и не
коммитить: запись умрёт вместе с откатом, то есть исчезнет ровно тогда, когда
она нужна. Записать и коммитить: адаптер зафиксирует ЗАОДНО всё, что сценарий
успел изменить и фиксировать не собирался, — то есть решит за него, где граница
транзакции.

Лежит в `repositories/` рядом с `SqlUserRepo`, потому что роль у него та же:
адаптер доступа к данным. Отличается только владелец транзакции — не сессия
сценария, а своя. Сценарий видит порт `WelcomeJournal` и про SQL не знает:
импорт `sqlalchemy` за пределами `infrastructure/db/` запрещён
(`scripts/check_db_access.py`).
"""

from app.infrastructure.db.autonomous_session import AutonomousSession
from app.infrastructure.db.models.welcome_attempt import WelcomeAttemptORM


class SqlWelcomeJournal:
    def __init__(self, autonomous: AutonomousSession) -> None:
        self._autonomous = autonomous

    async def record(self, user_id: int, outcome: str) -> None:
        async with self._autonomous.open() as session:
            session.add(WelcomeAttemptORM(user_id=user_id, outcome=outcome))
            # Коммитит вызывающий: `AutonomousSession.open` отдаёт сессию, а
            # границу своей короткой транзакции ставит тот, кто знает, что
            # именно он записал.
            await session.commit()
