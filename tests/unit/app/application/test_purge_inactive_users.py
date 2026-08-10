from datetime import UTC, datetime, timedelta

from app.application.use_cases.purge_inactive_users import PurgeInactiveUsersUseCase
from app.domain.models.user import User


async def _aged(users, days: int, *, active: bool, email: str) -> User:
    # Второе `save` обязательно: репозиторий отдаёт копию, и правка возвращённого
    # объекта до хранилища не доезжает — ровно как с настоящей базой, где
    # изменение доменной модели без сохранения не значит ничего.
    saved = await users.save(User(id=0, email=email, name="Кто-то", is_active=active))
    saved.created_at = datetime.now(tz=UTC) - timedelta(days=days)
    return await users.save(saved)


async def test_removes_only_stale_and_inactive(users, committer):
    stale = await _aged(users, 40, active=False, email="stale@example.com")
    fresh = await _aged(users, 1, active=False, email="fresh@example.com")
    old_active = await _aged(users, 40, active=True, email="active@example.com")

    removed = await PurgeInactiveUsersUseCase(users, committer).execute(keep_days=30)

    assert removed == 1
    assert await users.get_by_id(stale.id) is None
    assert await users.get_by_id(fresh.id) is not None
    assert await users.get_by_id(old_active.id) is not None
    assert committer.commits == 1


async def test_commits_even_when_nothing_matched(users, committer):
    """Коммит безусловный: пустая уборка обязана закрыть транзакцию так же,
    как непустая, иначе сессия уходит в закрытие с открытой транзакцией."""
    removed = await PurgeInactiveUsersUseCase(users, committer).execute(keep_days=30)

    assert removed == 0
    assert committer.commits == 1
