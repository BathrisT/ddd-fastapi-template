"""Сценарии. Один `provide` на сценарий — зависимости контейнер соберёт сам.

Сценарии привходовые, потому что привходовые их зависимости: репозиторий,
`Committer`, часы. Держать сценарий процессным нельзя — он утащил бы за собой
сессию первого запроса.

Забытая здесь строка не ломает сборку: контейнер валидирует только
зарегистрированные фабрики и про запрошенное входом не знает. Отказ наступил бы
в проде при первом обращении — поэтому есть отдельный тест
`tests/unit/app/composition/test_entry_dependencies.py`.
"""

from dishka import Provider, Scope, provide

from app.application.use_cases.list_users import ListUsersUseCase
from app.application.use_cases.purge_inactive_users import PurgeInactiveUsersUseCase
from app.application.use_cases.register_user import RegisterUserUseCase
from app.application.use_cases.welcome_user import WelcomeUserUseCase


class UseCaseProvider(Provider):
    scope = Scope.REQUEST

    register_user = provide(RegisterUserUseCase)
    list_users = provide(ListUsersUseCase)
    welcome_user = provide(WelcomeUserUseCase)
    purge_inactive_users = provide(PurgeInactiveUsersUseCase)
