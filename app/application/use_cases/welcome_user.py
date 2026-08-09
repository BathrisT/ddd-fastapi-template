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

from app.application.ports.committer import Committer
from app.application.ports.repositories.user_repo import UserRepo
from app.application.ports.services.ai_service import AiService


class WelcomeUserUseCase:
    def __init__(self, users: UserRepo, ai: AiService, committer: Committer) -> None:
        self._users = users
        self._ai = ai
        self._committer = committer

    async def execute(self, user_id: int) -> None:
        user = await self._users.get_by_id(user_id)
        if user is None:
            # Не отказ: пользователя могли удалить, пока задача ждала в очереди.
            logger.info("welcome_user: пользователя {} больше нет — пропуск", user_id)
            return
        if user.welcome_message is not None:
            return

        user.welcome_message = await self._ai.welcome_text(user.name)
        await self._users.save(user)
        await self._committer.commit()
