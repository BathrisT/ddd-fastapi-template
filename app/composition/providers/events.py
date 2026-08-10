"""Публикация событий и постановка задач: один клиент брокера на процесс.

Брокер приходит контекстом, а не создаётся здесь: CLI taskiq запускается как
`taskiq worker app.entrypoint_worker:broker` и требует готовый объект на уровне
модуля, поэтому его держит точка входа — ровно как настройки.

Обёртки-публикатора нет: маршрутизатор и есть реализация порта. Отдельный
класс, который только звал `EventRouter.dispatch`, лежал в инфраструктуре и
тянул её к входу очереди — из-за этого маршрут импортировал обработчики
локально, с `noqa` на каждой строке.

Правило — docs/rules/композиция-и-скоупы.md
"""

from dishka import Provider, Scope, from_context, provide
from taskiq import AsyncBroker

from app.application.ports.event_publisher import EventPublisher
from app.application.ports.job_results import JobResults
from app.application.ports.task_queue import TaskQueue
from app.application.ports.task_submitter import TaskSubmitter
from app.composition.event_router import EventRouter
from app.composition.task_router import TaskRouter
from app.infrastructure.worker.task_queue import TaskiqTaskQueue


class EventProvider(Provider):
    scope = Scope.APP

    broker = from_context(provides=AsyncBroker, scope=Scope.APP)

    taskiq_queue = provide(TaskiqTaskQueue)
    router = provide(EventRouter, provides=EventPublisher)
    tasks = provide(TaskRouter, provides=TaskSubmitter)

    # Два порта, один объект: ставит задачи и читает их результат один и тот же
    # клиент брокера. Псевдонимы, а не вторая сборка.

    @provide
    def queue(self, client: TaskiqTaskQueue) -> TaskQueue:
        return client

    @provide
    def job_results(self, client: TaskiqTaskQueue) -> JobResults:
        return client
