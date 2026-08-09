"""Порт репозитория → его реализация.

Привходовой скоуп: у каждого репозитория внутри сессия, а сессия живёт один
вход. Процессным репозиторий быть не может даже там, где выглядит безобидно —
он унёс бы с собой сессию в следующий запрос.

Когда репозиториев станет больше пятнадцати, файл делится на подпапку по
предметным областям (`repositories/users.py`, `repositories/billing.py`) —
проверяется `max_files_per_dir`.
"""

from dishka import Provider, Scope, provide

from app.application.ports.repositories.user_repo import UserRepo
from app.infrastructure.db.repositories.user_repo import SqlUserRepo


class RepositoryProvider(Provider):
    scope = Scope.REQUEST

    users = provide(SqlUserRepo, provides=UserRepo)
