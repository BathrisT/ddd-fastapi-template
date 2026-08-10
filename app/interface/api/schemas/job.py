from pydantic import BaseModel


class JobAccepted(BaseModel):
    """Работа принята. Идентификатор — ключ к `/jobs/{job_id}`."""

    job_id: str


class JobResponse(BaseModel):
    """Исход фоновой задачи: `pending` | `success` | `error`.

    `result` — то, что вернул обработчик, и его форму знает только он. Поэтому
    здесь `dict`, а не конкретная схема: одна ручка обслуживает все задачи.
    Цена этого решения названа в докстринге маршрута — раз содержимое общее,
    право смотреть обязано проверяться до, а не после.
    """

    status: str
    result: dict[str, object] | None = None
    error: str | None = None
