"""Доменный отказ → HTTP-код. Единственное место, которое знает про коды.

Перехватчик `Exception` в конце обязателен и стоит последним: без него
непойманное исключение уходит в стандартный обработчик Starlette, и клиент
получает голый 500 без строки в логе — то есть отказ, о котором никто не
узнает, пока не спросит пользователь.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.domain.exceptions import (
    AuthError,
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    # 400, а НЕ 422: 422 у FastAPI занят отказом схемы, и тело там другой
    # формы — `detail` списком объектов. Один код с двумя схемами уронил бы
    # клиента, читающего `detail[0].msg`, ровно там, где сервер объяснил
    # причину. Достижимо на шаблонной ручке: `{"name": "   "}`.
    @app.exception_handler(ValidationError)
    async def validation_handler(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ForbiddenError)
    async def forbidden_handler(_: Request, exc: ForbiddenError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(AuthError)
    async def auth_error_handler(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    # Базовый доменный отказ — последним из доменных: FastAPI выбирает
    # обработчик по точному типу, но наследники без своей строки попадут сюда.
    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on {} {}", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
