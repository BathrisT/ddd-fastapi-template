"""Транспортная половина проверки: достать доказательство из HTTP-заголовка.

Здесь лежит ровно то, что специфично для HTTP, — имя заголовка. Всё остальное
делает `CallerResolver` в `application/services/`, и это не педантизм: у
обработчика очереди то же доказательство приедет в `message.kwargs`, и если
резолюция осядет здесь, второй вход либо продублирует её, либо начнёт звать
эту функцию оттуда, где нет `Request`.

По той же причине гейт **не берёт репозиторий**. Соблазн есть — искать по ключу
всё равно придётся, — но это уже общая половина; правило проверяется
`scripts/check_composition.py`, и исключения для `guards/` в нём нет намеренно.

`Depends` тут законен и разрешён списком `[tool.composition].verifiers`: это не
внедрение зависимости, а объявление контракта входа. Способ проверки
принадлежит маршрутизации — там же, где выбирается обработчик.
"""

from typing import Annotated

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import Depends, Header

from app.application.dto.caller import Caller
from app.application.services.caller_resolver import CallerResolver


@inject
async def require_api_key(
    resolver: FromDishka[CallerResolver],
    x_api_key: Annotated[str | None, Header()] = None,
) -> Caller:
    """Отказ уезжает доменным `AuthError`, а не `HTTPException`.

    Код ответа знает один модуль — `exception_handlers.py`. Брось мы отсюда
    `HTTPException(401)`, знание о кодах расползлось бы по гейтам, и следующий
    выбрал бы 403 «потому что так понятнее».
    """
    return resolver.resolve(x_api_key)


CallerDep = Annotated[Caller, Depends(require_api_key)]
