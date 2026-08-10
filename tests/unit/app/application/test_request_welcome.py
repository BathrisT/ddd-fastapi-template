"""Постановка приветствия по требованию: номер задачи наружу, 404 до постановки."""

import pytest

from app.application.dto.tasks import TaskIntent, WelcomeUser
from app.application.use_cases.request_welcome import RequestWelcomeUseCase
from app.domain.exceptions import NotFoundError
from app.domain.models.user import User


class FakeTaskSubmitter:
    """Дублёр порта обычным классом: мок принял бы и вызов, которого в порте нет."""

    def __init__(self) -> None:
        self.submitted: list[TaskIntent] = []

    async def submit(self, intent: TaskIntent) -> str:
        self.submitted.append(intent)
        return "job-1"


async def test_returns_job_id_and_submits_the_intent(users):
    saved = await users.save(User(id=0, email="ann@example.com", name="Аня"))
    tasks = FakeTaskSubmitter()

    job_id = await RequestWelcomeUseCase(users=users, tasks=tasks).execute(saved.id)

    assert job_id == "job-1"
    assert tasks.submitted == [WelcomeUser(user_id=saved.id)]


async def test_missing_user_refuses_before_submitting(users):
    """404 до постановки, а не «задача принята» и тишина в ответ.

    Иначе клиент получил бы номер задачи, которая ничего не сделает, и
    опрашивал бы его до истечения TTL результата.
    """
    tasks = FakeTaskSubmitter()

    with pytest.raises(NotFoundError):
        await RequestWelcomeUseCase(users=users, tasks=tasks).execute(999)

    assert tasks.submitted == []
