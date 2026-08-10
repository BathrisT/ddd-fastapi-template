"""Шов проверки входа и опрос исхода фоновой задачи.

Очередь подменена: она уже проверена своими тестами, а Redis в интеграционном
прогоне не поднимается. Проверяется здесь другое — что гейт стоит на месте,
что отказ доезжает своим кодом и что идентификатор задачи возвращается наружу
и принимается обратно.
"""

import pytest
from dishka import Provider, Scope, provide

from app.application.dto.tasks import TaskIntent, WelcomeUser
from app.application.ports.event_publisher import EventPublisher
from app.application.ports.job_results import JobOutcome, JobResults
from app.application.ports.task_submitter import TaskSubmitter
from app.infrastructure.events.noop_publisher import NoopEventPublisher

_KEY = {"X-Api-Key": "test-api-key"}


class FakeTaskSubmitter:
    def __init__(self) -> None:
        self.submitted: list[TaskIntent] = []

    async def submit(self, intent: TaskIntent) -> str:
        self.submitted.append(intent)
        assert isinstance(intent, WelcomeUser)
        return "job-42"


class FakeJobResults:
    async def get(self, job_id: str) -> JobOutcome:
        if job_id == "job-42":
            return JobOutcome(status="success", result={"welcomed": True})
        return JobOutcome(status="pending")


class QueueStubProvider(Provider):
    @provide(scope=Scope.APP, override=True)
    def event_publisher(self) -> EventPublisher:
        return NoopEventPublisher()

    @provide(scope=Scope.APP, override=True)
    def tasks(self) -> TaskSubmitter:
        return FakeTaskSubmitter()

    @provide(scope=Scope.APP, override=True)
    def job_results(self) -> JobResults:
        return FakeJobResults()


@pytest.fixture
def entry_providers() -> list[Provider]:
    return [QueueStubProvider()]


async def test_without_key_it_is_401(client):
    created = await client.post(
        "/users", json={"email": "ann@example.com", "name": "Аня"}, headers=_KEY
    )

    response = await client.post(f"/users/{created.json()['id']}/welcome")

    assert response.status_code == 401


async def test_wrong_key_is_401(client):
    response = await client.get("/jobs/job-42", headers={"X-Api-Key": "wrong-key"})

    assert response.status_code == 401


async def test_welcome_returns_job_id_and_it_polls(client):
    """Полный круг: приняли работу — вернули номер — по номеру отдали исход."""
    created = await client.post(
        "/users", json={"email": "ann@example.com", "name": "Аня"}, headers=_KEY
    )
    user_id = created.json()["id"]

    accepted = await client.post(f"/users/{user_id}/welcome", headers=_KEY)

    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]

    polled = await client.get(f"/jobs/{job_id}", headers=_KEY)

    assert polled.status_code == 200
    assert polled.json() == {"status": "success", "result": {"welcomed": True}, "error": None}


async def test_welcome_for_missing_user_is_404(client):
    """Отказ до постановки: иначе клиент получил бы номер задачи, которой нет смысла."""
    response = await client.post("/users/999999/welcome", headers=_KEY)

    assert response.status_code == 404


async def test_unknown_job_is_pending_not_404(client):
    """Неизвестный номер — `pending`, а не 404.

    Очередь не различает «ещё не выполнена» и «никогда не существовала»:
    результат появляется только по завершении. Отдавать 404 значило бы врать
    клиенту, который опрашивает свою честно поставленную задачу.
    """
    response = await client.get("/jobs/unknown-job", headers=_KEY)

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
