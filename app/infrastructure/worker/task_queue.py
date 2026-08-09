"""Постановка задачи в очередь taskiq по имени и чтение её результата.

`find_task` возвращает `None` для незарегистрированного имени — это тот самый
отказ, который при декораторе-регистраторе наступал в проде и выглядел как
«задача просто не выполнилась». Здесь он громкий и на месте постановки.
"""

from taskiq import AsyncBroker

from app.application.ports.job_results import JobOutcome


class TaskiqTaskQueue:
    def __init__(self, broker: AsyncBroker) -> None:
        self._broker = broker

    async def enqueue(self, task_name: str, **kwargs: object) -> str:
        task = self._broker.find_task(task_name)
        if task is None:
            raise RuntimeError(f"Задача «{task_name}» не зарегистрирована в реестре")
        queued = await task.kiq(**kwargs)
        return queued.task_id

    async def get(self, job_id: str) -> JobOutcome:
        backend = self._broker.result_backend
        if not await backend.is_result_ready(job_id):
            return JobOutcome(status="pending")
        result = await backend.get_result(job_id)
        if result.is_err:
            return JobOutcome(status="error", error=str(result.error))
        value = result.return_value
        return JobOutcome(status="success", result=value if isinstance(value, dict) else None)
