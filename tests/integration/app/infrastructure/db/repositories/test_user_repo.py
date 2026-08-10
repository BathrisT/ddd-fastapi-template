from datetime import UTC, datetime, timedelta

import pytest

from app.domain.exceptions import ConflictError
from app.domain.models.user import User
from app.infrastructure.db.repositories.user_repo import SqlUserRepo


async def test_save_assigns_id_and_created_at(session):
    passed = User(id=0, email="ann@example.com", name="Аня")

    saved = await SqlUserRepo(session).save(passed)

    assert saved.id != 0
    assert saved.created_at is not None
    assert saved.is_active is False
    # У ПЕРЕДАННОГО объекта идентификатор так и остался нулевым: его присвоила
    # база возвращённому. Это и есть причина правила «работают с возвращённым»
    # (CLAUDE.md, «Идентификаторы») — событие или задача, собранные из
    # аргумента, уехали бы с `user_id=0` в никуда, не упав на месте.
    assert passed.id == 0


async def test_duplicate_email_raises_conflict(session):
    """Уникальность стережёт база, а не проверка в сценарии.

    Проверка «есть ли уже такой» и вставка — две операции, между которыми
    успевает влезть параллельный запрос. Без индекса в таблице оказались бы
    двое с одной почтой, и заметили бы это через месяц.
    """
    repo = SqlUserRepo(session)
    await repo.save(User(id=0, email="ann@example.com", name="Аня"))

    with pytest.raises(ConflictError):
        await repo.save(User(id=0, email="ann@example.com", name="Другая Аня"))


async def test_purge_spares_active_users(session):
    """Уборка сносит только неподтверждённых — условие принадлежит запросу."""
    repo = SqlUserRepo(session)
    stale = await repo.save(User(id=0, email="stale@example.com", name="Протухший"))
    active = await repo.save(User(id=0, email="active@example.com", name="Живой", is_active=True))
    # created_at ставит база (server_default), поэтому «состарить» строки можно
    # только сдвинув границу вперёд — так же, как это увидит сценарий.
    cutoff = datetime.now(tz=UTC) + timedelta(minutes=1)

    removed = await repo.delete_inactive_before(cutoff)

    assert removed == 1
    assert await repo.get_by_id(stale.id) is None
    assert await repo.get_by_id(active.id) is not None


async def test_list_recent_is_newest_first(session):
    repo = SqlUserRepo(session)
    first = await repo.save(User(id=0, email="one@example.com", name="Первый"))
    second = await repo.save(User(id=0, email="two@example.com", name="Второй"))

    listed = await repo.list_recent(limit=10)

    assert [u.id for u in listed] == [second.id, first.id]
