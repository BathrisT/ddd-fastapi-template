"""Корень HTTP-входа: все маршруты приложения одним роутером.

Собирать роутеры в фабрике приложения нельзя: новый пакет маршрутов надо было
бы не забыть туда вписать, а забывчивость даёт не ошибку сборки, а отсутствие
ручки в проде. Здесь сосед по пакету обязан быть назван, и это проверяется
(`scripts/check_package_coverage.py`).

Порядок значим: у FastAPI побеждает первый совпавший путь.
"""

from fastapi import APIRouter

from app.interface.api.routes.health import router as health_router
from app.interface.api.routes.jobs import router as jobs_router
from app.interface.api.routes.users import router as users_router

router = APIRouter()
router.include_router(health_router)
router.include_router(jobs_router)
router.include_router(users_router)
