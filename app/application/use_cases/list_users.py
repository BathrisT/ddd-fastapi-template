from app.application.ports.repositories.user_repo import UserRepo
from app.domain.models.user import User

# Потолок выдачи живёт здесь, а не в схеме запроса: слой входа отвечает за
# форму, а «сколько мы готовы отдать за раз» — правило приложения, и оно
# обязано работать одинаково для любого входа.
_MAX_LIMIT = 100


class ListUsersUseCase:
    def __init__(self, users: UserRepo) -> None:
        self._users = users

    async def execute(self, limit: int) -> list[User]:
        return await self._users.list_recent(min(limit, _MAX_LIMIT))
