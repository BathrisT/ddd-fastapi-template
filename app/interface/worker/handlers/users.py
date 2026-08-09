"""Обработчики очереди для пользователей.

Обработчик — вход, а не место для логики: принять работу, распаковать аргументы,
позвать сценарий (правило 7 композиции). Всё, что появляется тут сверх этого,
оказывается
непроверяемым — тест на такое пришлось бы писать через очередь.

Аргументы задач только именованные: провайдер арендатора очереди читает
`message.kwargs`, а позиционные до него не доезжают.
"""

from dishka import FromDishka
from loguru import logger

from app.application.ports.key_guard import KeyGuard
from app.application.use_cases.purge_inactive_users import PurgeInactiveUsersUseCase
from app.application.use_cases.welcome_user import WelcomeUserUseCase

# Сколько живёт неподтверждённая регистрация. Пока значение читает одно место —
# константа живёт здесь. Как только решать «что считать протухшим» начнёт
# кто-то ещё, оно переезжает в `domain/catalog/policy.py`: правило бизнеса,
# разложенное константами по файлам, — это скопированное правило (правило 7
# раскладки, CLAUDE.md).
_KEEP_DAYS = 30

# Короче тика расписания, и это выбор в пользу самовосстановления: процесс,
# убитый между `claim` и `release`, оставил бы ключ висеть, и уборка молча
# перестала бы ходить совсем. TTL короче тика гарантирует, что к следующему
# тику ключ протух сам.
#
# Цена выбора названа честно в докстринге обработчика: прогон ДЛИННЕЕ TTL
# теряет замок на ходу, и следующий тик возьмёт свежий. Продлевать захват
# по ходу работы (heartbeat) шаблон не умеет — понадобится, когда уборка
# начнёт идти дольше часа.
_LOCK_KEY = "lock:purge_inactive_users"
_LOCK_TTL_S = 50 * 60


async def welcome_user(user_id: int, use_case: FromDishka[WelcomeUserUseCase]) -> None:
    await use_case.execute(user_id)


async def purge_inactive_users(
    use_case: FromDishka[PurgeInactiveUsersUseCase],
    guard: FromDishka[KeyGuard],
) -> None:
    """Уборка по расписанию.

    Замок — не перестраховка: тик может прийти дважды (несколько реплик
    шедулера, перезапуск с пересдачей сообщения), и два прогона начнут удалять
    одни и те же строки, блокируя друг друга на них же.

    От прогона, который идёт дольше `_LOCK_TTL_S`, замок НЕ защищает: ключ к
    тому времени протухнет, и следующий тик спокойно возьмёт свежий. Это
    сознательный размен в пользу самовосстановления — см. комментарий у
    константы. Пока уборка укладывается в TTL, разницы нет; перестанет
    укладываться — нужен heartbeat, а не просто больший TTL.
    """
    token = await guard.claim(_LOCK_KEY, _LOCK_TTL_S)
    if token is None:
        # Не debug: в проде уровень INFO, и залипший ключ (процесс убит между
        # claim и release) выглядел бы как «уборка просто перестала ходить»
        # при абсолютно пустом логе.
        logger.warning("purge_inactive_users: прошлый прогон ещё идёт — пропуск тика")
        return
    try:
        await use_case.execute(keep_days=_KEEP_DAYS)
    finally:
        await guard.release(_LOCK_KEY, token)
