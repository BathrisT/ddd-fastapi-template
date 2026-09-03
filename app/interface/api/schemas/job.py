from pydantic import BaseModel


class JobAccepted(BaseModel):
    """Работа принята. Идентификатор — ключ к `/jobs/{job_id}`."""

    job_id: str


class JobResponse(BaseModel):
    """Исход фоновой задачи: `pending` | `success` | `error`.

    `result` — `dict`, а не схема: форму знает только обработчик, а ручка одна
    на все задачи. Цена названа в докстринге маршрута.
    """

    status: str
    result: dict[str, object] | None = None
    error: str | None = None
