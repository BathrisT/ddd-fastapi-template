from typing import Annotated

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Query

from app.application.use_cases.list_users import ListUsersUseCase
from app.application.use_cases.register_user import RegisterUserCommand, RegisterUserUseCase
from app.application.use_cases.request_welcome import RequestWelcomeUseCase
from app.interface.api.guards.api_key import CallerDep
from app.interface.api.schemas.job import JobAccepted
from app.interface.api.schemas.user import UserCreate, UserResponse

# route_class обязателен на ЛИСТОВОМ роутере: без него `FromDishka` в хендлере
# молча не сработает, а отказ выглядит как «параметр не пришёл».
router = APIRouter(prefix="/users", tags=["users"], route_class=DishkaRoute)


@router.post("", status_code=201)
async def register_user(
    payload: UserCreate,
    use_case: FromDishka[RegisterUserUseCase],
) -> UserResponse:
    user = await use_case.execute(RegisterUserCommand(email=payload.email, name=payload.name))
    return UserResponse.model_validate(user)


@router.get("")
async def list_users(
    use_case: FromDishka[ListUsersUseCase],
    # Только нижняя граница: она про форму запроса — «ноль страниц» и «минус
    # десять» не значат ничего ни при каком потолке. Верхнюю сюда не ставим,
    # её держит сценарий (`list_users._MAX_LIMIT`), и повтори мы её
    # здесь — правило жило бы в двух местах, а поднятие потолка в сценарии
    # молча не доехало бы до HTTP.
    limit: Annotated[int, Query(ge=1)] = 20,
) -> list[UserResponse]:
    users = await use_case.execute(limit)
    return [UserResponse.model_validate(user) for user in users]


@router.post("/{user_id}/welcome", status_code=202)
async def request_welcome(
    user_id: int,
    _: CallerDep,
    use_case: FromDishka[RequestWelcomeUseCase],
) -> JobAccepted:
    """202, а не 201: ресурс не создан, работа только принята.

    Ответ несёт идентификатор задачи — по нему клиент опрашивает `/jobs/{id}`,
    пока крутится прогресс. Это единственный вид задачи, у которой исход ждут;
    приветствие после регистрации ставится событием, и его никто не опрашивает.
    """
    job_id = await use_case.execute(user_id)
    return JobAccepted(job_id=job_id)
