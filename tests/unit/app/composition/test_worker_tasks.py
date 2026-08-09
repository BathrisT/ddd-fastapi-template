"""Реестр задач: имена уникальны, расписание ссылается на зарегистрированное.

Первый тест появился вместе с отказом от словаря имён: пока имя задавалось
енумом, два обработчика не могли получить одно имя по построению. Теперь имя —
это имя функции, и два одноимённых обработчика в разных модулях молча
перетёрли бы друг друга в реестре брокера.

Второй закрывает то, что стерёг удалённый `check_worker_tasks.py`: расписание
ссылается на задачу, которой в реестре нет, — раньше это был отказ в проде при
первом срабатывании, а не ошибка сборки.
"""

from __future__ import annotations

from taskiq import InMemoryBroker

from app.composition.worker_tasks import WorkerTasks
from app.interface.worker.schedule import SCHEDULE


def test_task_names_are_unique() -> None:
    names = [handler.__name__ for handler in WorkerTasks.TABLE]
    duplicates = {name for name in names if names.count(name) > 1}
    assert duplicates == set()


def test_schedule_references_registered_tasks() -> None:
    broker = InMemoryBroker()
    WorkerTasks.register(broker)

    unknown = [
        item.handler.__name__
        for item in SCHEDULE
        if broker.find_task(item.handler.__name__) is None
    ]
    assert unknown == []
