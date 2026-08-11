import pytest

from app.application.use_cases.register_user import RegisterUserCommand, RegisterUserUseCase
from app.domain.events.user_registered import UserRegistered
from app.domain.exceptions import ConflictError, ValidationError


def _use_case(users, committer, publisher):
    return RegisterUserUseCase(users_repo=users, committer=committer, events=publisher)


async def test_registers_and_publishes(users, committer, publisher):
    user = await _use_case(users, committer, publisher).execute(
        RegisterUserCommand(email="Ann@Example.COM ", name=" Аня ")
    )

    assert user.id != 0
    # Почта нормализуется, иначе «Ann@» и «ann@» станут двумя людьми
    assert user.email == "ann@example.com"
    assert user.name == "Аня"
    assert committer.commits == 1
    assert publisher.events == [UserRegistered(user_id=user.id)]


async def test_event_goes_out_after_commit(users, committer, publisher):
    """Событие ставит задачу, которая читает базу своей сессией.

    Опубликуй его до фиксации — воркер успеет прочитать строку, которой ещё
    нет, и упадёт «не найдено» на том, что вот-вот появится.
    """
    order: list[str] = []
    original_commit = committer.commit
    original_publish = publisher.publish

    async def track_commit():
        order.append("commit")
        await original_commit()

    async def track_publish(event):
        order.append("publish")
        await original_publish(event)

    committer.commit = track_commit
    publisher.publish = track_publish

    await _use_case(users, committer, publisher).execute(
        RegisterUserCommand(email="ann@example.com", name="Аня")
    )

    assert order == ["commit", "publish"]


async def test_duplicate_email_is_rejected(users, committer, publisher):
    use_case = _use_case(users, committer, publisher)
    await use_case.execute(RegisterUserCommand(email="ann@example.com", name="Аня"))

    with pytest.raises(ConflictError):
        await use_case.execute(RegisterUserCommand(email="ANN@example.com", name="Аня вторая"))

    assert committer.commits == 1
    assert len(publisher.events) == 1


async def test_blank_name_is_rejected(users, committer, publisher):
    with pytest.raises(ValidationError):
        await _use_case(users, committer, publisher).execute(
            RegisterUserCommand(email="ann@example.com", name="   ")
        )

    assert committer.commits == 0
    assert publisher.events == []
