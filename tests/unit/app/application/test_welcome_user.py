import pytest

from app.application.use_cases.welcome_user import WelcomeUserUseCase
from app.domain.models.user import User


class FakeJournal:
    """Дублёр журнала: обычный класс, а не мок.

    Мок принял бы и тот вызов, которого в порте уже нет, и тест остался бы
    зелёным после переименования метода.
    """

    def __init__(self) -> None:
        self.records: list[tuple[int, str]] = []

    async def record(self, user_id: int, outcome: str) -> None:
        self.records.append((user_id, outcome))


class FreeGuard:
    """Замок всегда свободен: гонку проверяет линза, не этот тест."""

    async def claim(self, key: str, ttl_seconds: int) -> str | None:
        return "token"

    async def release(self, key: str, token: str) -> None:
        return None


class BusyGuard:
    """Ключ занят: другой прогон уже готовит это приветствие."""

    async def claim(self, key: str, ttl_seconds: int) -> str | None:
        return None

    async def release(self, key: str, token: str) -> None:
        raise AssertionError("освобождать нечего: захвата не было")


def _use_case(users, ai, committer, journal=None, guard=None):
    return WelcomeUserUseCase(
        users=users,
        ai=ai,
        committer=committer,
        journal=journal or FakeJournal(),
        guard=guard or FreeGuard(),
    )


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


async def test_busy_lock_means_another_run_is_already_paying(users, ai, committer):
    """Занятый ключ — не отказ, а «этим уже занимаются».

    Проверка `welcome_message` читает и пишет разными шагами, поэтому два
    прогона успевают оба увидеть `None` до первого коммита и оба сходить в
    платную модель. Замок разводит их до чтения.
    """
    user = await users.save(User(id=0, email="ann@example.com", name="Аня"))

    outcome = await _use_case(users, ai, committer, guard=BusyGuard()).execute(user.id)

    assert outcome.status == "busy"
    assert ai.calls == []
    assert (await users.get_by_id(user.id)).welcome_message is None


async def test_outcome_tells_the_poller_what_happened(users, ai, committer):
    """Опрашивающий `/jobs/{id}` обязан отличать «сделал» от «не делал».

    Пока обработчик возвращал `None`, очередь отмечала успехом все исходы
    подряд, и прогресс закрывался там, где работы не было.
    """
    user = await users.save(User(id=0, email="ann@example.com", name="Аня"))

    first = await _use_case(users, ai, committer).execute(user.id)
    second = await _use_case(users, ai, committer).execute(user.id)
    gone = await _use_case(users, ai, committer).execute(999)

    assert first.status == "welcomed"
    assert first.message
    assert second.status == "already"
    assert gone.status == "gone"
    assert len(ai.calls) == 1


class BrokenAi:
    """Модель отказала — платить не пришлось, но попытка была."""

    async def welcome_text(self, name: str) -> str:
        raise RuntimeError("модель недоступна")


async def test_failed_call_is_journalled_and_reraised(users, committer):
    """Провалившееся обращение оставляет след, а отказ едет наверх.

    След нужен, чтобы было видно, что попытка была: иначе разница между
    «не пробовали» и «пробовали и не вышло» теряется. А глушить исключение
    нельзя — задача обязана завершиться ошибкой, а не тихим успехом.
    """
    user = await users.save(User(id=0, email="ann@example.com", name="Аня"))
    journal = FakeJournal()

    with pytest.raises(RuntimeError):
        await _use_case(users, BrokenAi(), committer, journal=journal).execute(user.id)

    assert journal.records == [(user.id, "error")]
    assert (await users.get_by_id(user.id)).welcome_message is None
