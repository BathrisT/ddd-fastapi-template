"""Сценарий: сочинить приветствие новому пользователю и сохранить его.

В настоящем проекте на этом месте стоит отправка письма, а поле — журнал
доставки: «кому и что ушло». Сохранение вместо отправки оставлено, чтобы шаблон
не тянул за собой почтовый шлюз; форма сценария от этого не меняется.

Идемпотентен намеренно: пользователь с уже готовым приветствием второй раз в
модель не идёт. Повторная доставка сообщения очереди — штатное событие, а не
сбой, и без этой проверки каждая пересдача стоила бы ещё одного платного
обращения.

**Снимок пользователя держится через обращение к модели, а `save()`
перезаписывает строку целиком** (см. контракт в порте `UserRepo`). Пока
пишущий сценарий один — а в шаблоне он один, — потерять нечего. Заводя
второго (первым обычно появляется подтверждение почты, ставящее
`is_active=True`), это место придётся пересобрать: подтверждение, пришедшее в
те секунды, что мы ждали модель, вернулось бы в базу отменённым, и уборка
неподтверждённых снесла бы подтверждённого. Перечитать `get_by_id` для этого
недостаточно — в той же сессии он отдаст объект из identity map, не сходив в
базу.
"""

from loguru import logger

from app.application.dto.welcome_outcome import WelcomeOutcome
from app.application.ports.committer import Committer
from app.application.ports.key_guard import KeyGuard
from app.application.ports.repositories.user_repo import UserRepo
from app.application.ports.services.ai_service import AiService
from app.application.ports.welcome_journal import WelcomeJournal

# Ключ на пользователя, а не на задачу: две задачи по одному пользователю —
# ровно то, что надо развести.
_LOCK_PREFIX = "lock:welcome_user:"
# Заметно длиннее ожидания модели (`LLM__TIMEOUT`, по умолчанию 60 с):
# истёкший на середине замок пустил бы второй прогон в ту же оплату.
_LOCK_TTL_S = 5 * 60


class WelcomeUserUseCase:
    def __init__(
        self,
        users: UserRepo,
        ai: AiService,
        committer: Committer,
        journal: WelcomeJournal,
        guard: KeyGuard,
    ) -> None:
        self._users = users
        self._ai = ai
        self._committer = committer
        self._journal = journal
        self._guard = guard

    async def execute(self, user_id: int) -> WelcomeOutcome:
        # Замок, а не только проверка `welcome_message`: та читает и пишет
        # разными шагами, и два прогона успевают оба увидеть `None` до
        # того, как первый закоммитит. Пока приветствие ставилось лишь
        # событием при регистрации, это требовало редкой пересдачи
        # сообщения; ручка `POST /users/{id}/welcome` делает гонку
        # достижимой двойным кликом — а цена ей платное обращение к
        # модели. Тот же приём и по той же причине, что у уборки по
        # расписанию (`purge_inactive_users`).
        token = await self._guard.claim(f"{_LOCK_PREFIX}{user_id}", _LOCK_TTL_S)
        if token is None:
            logger.info("welcome_user: приветствие {} уже готовится — пропуск", user_id)
            # Отличимый исход, а не молчаливый успех: опрашивающий этот
            # номер иначе закрыл бы прогресс на работе, которую делает
            # другой прогон, и не узнал бы, если та упадёт.
            return WelcomeOutcome(status="busy")
        try:
            return await self._welcome(user_id)
        finally:
            await self._guard.release(f"{_LOCK_PREFIX}{user_id}", token)

    async def _welcome(self, user_id: int) -> WelcomeOutcome:
        user = await self._users.get_by_id(user_id)
        if user is None:
            # Не отказ: пользователя могли удалить, пока задача ждала в очереди.
            logger.info("welcome_user: пользователя {} больше нет — пропуск", user_id)
            return WelcomeOutcome(status="gone")
        if user.welcome_message is not None:
            return WelcomeOutcome(status="already", message=user.welcome_message)

        # Обращение к модели платное и уже случилось — что бы дальше ни
        # произошло с транзакцией. Поэтому след пишется СРАЗУ и своей
        # транзакцией (`AutonomousSession` за портом): упади сохранение ниже,
        # сценарий откатится, а отметка «мы за это заплатили» уцелеет.
        # Провалившееся обращение отмечается по той же причине — чтобы было
        # видно, что попытка была.
        try:
            message = await self._ai.welcome_text(user.name)
        except Exception:
            await self._journal.record(user_id, "error")
            raise
        await self._journal.record(user_id, "success")

        user.welcome_message = message
        await self._users.save(user)
        await self._committer.commit()

        return WelcomeOutcome(status="welcomed", message=message)
