from app.application.use_cases.welcome_user import WelcomeUserUseCase
from app.domain.models.user import User


def _use_case(users, ai, committer):
    return WelcomeUserUseCase(users=users, ai=ai, committer=committer)


async def test_writes_welcome(users, ai, committer):
    user = await users.save(User(id=0, email="ann@example.com", name="Аня"))

    await _use_case(users, ai, committer).execute(user.id)

    assert (await users.get_by_id(user.id)).welcome_message == "Привет!"
    assert ai.calls == ["Аня"]
    assert committer.commits == 1


async def test_second_delivery_does_not_call_the_model(users, ai, committer):
    """Повторная доставка сообщения очереди — штатное событие.

    Без этой проверки каждая пересдача стоила бы ещё одного платного обращения
    к модели, а текст приветствия менялся бы у уже поприветствованного.
    """
    user = await users.save(User(id=0, email="ann@example.com", name="Аня"))
    use_case = _use_case(users, ai, committer)
    await use_case.execute(user.id)

    await use_case.execute(user.id)

    assert ai.calls == ["Аня"]
    assert committer.commits == 1


async def test_missing_user_is_not_a_failure(users, ai, committer):
    """Пользователя могли удалить, пока задача ждала в очереди."""
    await _use_case(users, ai, committer).execute(404)

    assert ai.calls == []
    assert committer.commits == 0
