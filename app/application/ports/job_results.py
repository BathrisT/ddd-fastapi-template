"""Результат поставленной задачи — для тех, кто её ставил и хочет знать исход.

Отдельно от постановки: ставят задачи многие, а опрашивает результат только
длинная операция, запущенная из интерфейса, — импорт файла, пакетная выгрузка,
обращение к модели по кнопке, — у которой в UI крутится прогресс. Задача,
поставленная событием (как `welcome_user` в шаблоне), исхода не опрашивает: её
никто не ждёт.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class JobOutcome:
    status: str  # pending | success | error
    result: dict[str, object] | None = None
    error: str | None = None


class JobResults(Protocol):
    async def get(self, job_id: str) -> JobOutcome: ...
