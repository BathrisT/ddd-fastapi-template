from typing import Annotated

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Query

from app.application.use_cases.list_users import ListUsersUseCase
from app.application.use_cases.register_user import RegisterUserCommand, RegisterUserUseCase
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
