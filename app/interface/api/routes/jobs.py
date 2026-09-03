"""Опрос исхода фоновой задачи: длинная операция с прогрессом в UI.

Маршрут закрыт гейтом: неугадываемость номера — не право доступа, он уезжает
в историю браузера, `Referer`, лог nginx и Sentry.

ВЛАДЕЛЬЦА ЗАДАЧИ ЗДЕСЬ НИКТО НЕ ПРОВЕРЯЕТ — дыра оставлена сознательно, пока
звонящий один на приложение. Станет несколько — проверка встаёт ровно сюда,
`Caller` уже приходит в хендлер.

Порт берётся без сценария намеренно: тут нет ни правила, ни транзакции. И
помни, что `success` значит «задача завершилась», а не «сделала работу»:
упёршаяся в замок вторая задача выходит без исключения.
"""

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

from app.application.ports.job_results import JobResults
from app.interface.api.guards.api_key import CallerDep
from app.interface.api.schemas.job import JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"], route_class=DishkaRoute)


@router.get("/{job_id}")
async def get_job(job_id: str, _: CallerDep, results: FromDishka[JobResults]) -> JobResponse:
    outcome = await results.get(job_id)
    return JobResponse(status=outcome.status, result=outcome.result, error=outcome.error)
