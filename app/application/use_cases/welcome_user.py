"""Сценарий: сочинить приветствие новому пользователю и сохранить его.

В настоящем проекте здесь стоит отправка письма; сохранение оставлено, чтобы
шаблон не тянул почтовый шлюз. Идемпотентен намеренно: повторная доставка
сообщения — штатное событие, и без проверки каждая пересдача стоила бы ещё
одного платного обращения.

Снимок пользователя держится через вызов модели, а `save()` пишет строку
целиком. Пока пишущий сценарий один, терять нечего; заводя второго (обычно
это подтверждение почты), место придётся пересобрать — иначе подтверждение,
пришедшее за те секунды, вернётся в базу отменённым.
"""

from loguru import logger

from app.application.dto.welcome_outcome import WelcomeOutcome
from app.application.ports.committer import Committer
from app.application.ports.key_guard import KeyGuard
from app.application.ports.repositories.user_repo import UserRepo
from app.application.ports.repositories.welcome_attempt_repo import WelcomeAttemptRepo
from app.application.ports.services.ai_service import AiService

# Ключ на пользователя: две задачи по одному — то, что надо развести.
_LOCK_PREFIX = "lock:welcome_user:"
# Длиннее ожидания модели: истёкший замок пустил бы второй прогон в оплату.
_LOCK_TTL_S = 5 * 60


class WelcomeUserUseCase:
    def __init__(
        self,
        users_repo: UserRepo,
        ai: AiService,
        committer: Committer,
        journal_repo: WelcomeAttemptRepo,
        guard: KeyGuard,
    ) -> None:
        self._users_repo = users_repo
        self._ai = ai
        self._committer = committer
        self._journal_repo = journal_repo
        self._guard = guard

    async def execute(self, user_id: int) -> WelcomeOutcome:
        # Замок, а не только проверка `welcome_message`: та читает и пишет
        # разными шагами, и два прогона успевают увидеть `None` оба. Гонка
        # достижима двойным кликом, а цена ей — платное обращение.
        token = await self._guard.claim(f"{_LOCK_PREFIX}{user_id}", _LOCK_TTL_S)
        if token is None:
            logger.info("welcome_user: приветствие {} уже готовится — пропуск", user_id)
            # Отличимый исход: иначе опрашивающий закрыл бы прогресс
            # на чужой работе и не узнал бы о её падении.
            return WelcomeOutcome(status="busy")
        try:
            return await self._welcome(user_id)
        finally:
            await self._guard.release(f"{_LOCK_PREFIX}{user_id}", token)

    async def _welcome(self, user_id: int) -> WelcomeOutcome:
        user = await self._users_repo.get_by_id(user_id)
        if user is None:
            # Не отказ: пользователя могли удалить, пока задача ждала в очереди.
            logger.info("welcome_user: пользователя {} больше нет — пропуск", user_id)
            return WelcomeOutcome(status="gone")
        if user.welcome_message is not None:
            return WelcomeOutcome(status="already", message=user.welcome_message)

        # Платное обращение уже случилось, поэтому след пишется СРАЗУ и своей
        # транзакцией: упади сохранение ниже, сценарий откатится, а отметка
        # «мы за это заплатили» уцелеет. Провал отмечается по той же причине.
        try:
            message = await self._ai.welcome_text(user.name)
        except Exception:
            await self._journal_repo.record(user_id, "error")
            raise
        await self._journal_repo.record(user_id, "success")

        user.welcome_message = message
        await self._users_repo.save(user)
        await self._committer.commit()

        return WelcomeOutcome(status="welcomed", message=message)
