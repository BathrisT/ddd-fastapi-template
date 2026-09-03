"""Обработчики очереди: принять, распаковать, позвать сценарий.

Логика тут непроверяема — тест пришлось бы писать через очередь. Аргументы
только именованные: провайдер арендатора читает `message.kwargs`.
"""

from dishka import FromDishka
from loguru import logger

from app.application.ports.key_guard import KeyGuard
from app.application.use_cases.purge_inactive_users import PurgeInactiveUsersUseCase
from app.application.use_cases.welcome_user import WelcomeUserUseCase

# Появится второй читатель — переезжает в `domain/catalog/policy.py`.
_KEEP_DAYS = 30

# Короче тика: иначе процесс, убитый между `claim` и `release`, оставил бы
# ключ висеть, и уборка молча перестала бы ходить.
_LOCK_KEY = "lock:purge_inactive_users"
_LOCK_TTL_S = 50 * 60


async def welcome_user(user_id: int, use_case: FromDishka[WelcomeUserUseCase]) -> dict[str, object]:
    """Исход читает тот, кто опрашивает `/jobs/{id}`: с `None` очередь
    отметит успехом и «сделал», и «занят другой», и «нет такого».
    """
    outcome = await use_case.execute(user_id)
    return {"status": outcome.status, "message": outcome.message}


async def purge_inactive_users(
    use_case: FromDishka[PurgeInactiveUsersUseCase],
    guard: FromDishka[KeyGuard],
) -> None:
    """Уборка по расписанию. Замок не перестраховка: при нескольких репликах
    шедулера тик приходит дважды, и прогоны блокируют друг друга на строках.
    """
    token = await guard.claim(_LOCK_KEY, _LOCK_TTL_S)
    if token is None:
        # Не debug: в проде уровень INFO, и залипший ключ выглядел бы как
        # «уборка перестала ходить» при пустом логе.
        logger.warning("purge_inactive_users: прошлый прогон ещё идёт — пропуск тика")
        return
    try:
        await use_case.execute(keep_days=_KEEP_DAYS)
    finally:
        await guard.release(_LOCK_KEY, token)
