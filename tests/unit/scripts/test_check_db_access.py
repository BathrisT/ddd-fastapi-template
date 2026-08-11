"""Граница базы: сессию открывает композиция, SQL пишет репозиторий.

Четыре правила в одном сторожe, и самое хрупкое из них — последнее: репозиторий
опознают ПО ИМЕНИ ТИПА сразу три проверки, поэтому порт, названный
`WelcomeJournal`, делает слепыми и `check_composition`, и `check_n_plus_one`,
не сообщая об этом ни одной из них.
"""

from tests.unit.scripts.conftest import Repo

DB_ACCESS = """
[tool.db_access]
session_markers = ["AsyncSession", "async_sessionmaker"]
session_owners = ["app/composition/providers", "app/infrastructure/db/autonomous_session.py"]
orm_packages = ["sqlalchemy", "alembic"]
orm_allowed = ["app/infrastructure/db", "app/composition/providers"]
orm_models = "app/infrastructure/db/models"
repository_ports = "app/application/ports/repositories"
provider_dirs = ["app/composition/providers"]
"""


def prepare(repo: Repo) -> None:
    repo.pyproject(DB_ACCESS)
    repo.write("app/infrastructure/db/models/__init__.py")
    repo.write("app/application/ports/repositories/__init__.py")
    repo.write("app/composition/providers/__init__.py")


class TestSessionOwners:
    def test_session_opened_outside_composition(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/services/repair.py",
            """
            class Repair:
                async def run(self) -> None:
                    session = AsyncSession()
                    await session.commit()
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 1
        assert result.mentions("сессия создаётся вне композиции")

    def test_aliased_session_is_the_same_session(self, repo: Repo) -> None:
        """`from ... import AsyncSession as S` обходил сверку по имени."""
        prepare(repo)
        repo.write(
            "app/application/services/repair.py",
            """
            from sqlalchemy.ext.asyncio import AsyncSession as S


            class Repair:
                async def run(self) -> None:
                    session = S()
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 1
        assert result.mentions("сессия создаётся вне композиции")

    def test_owner_may_open_it(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/composition/providers/request.py",
            """
            from sqlalchemy.ext.asyncio import AsyncSession


            class RequestProvider:
                def session(self, engine) -> AsyncSession:
                    return AsyncSession(bind=engine)
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 0


class TestOrmStaysInRepositories:
    def test_sqlalchemy_import_outside_the_data_layer(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/use_cases/report.py",
            """
            import sqlalchemy as sa


            class ReportUseCase:
                async def execute(self) -> None:
                    sa.select()
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 1
        assert result.mentions("вне слоя доступа к данным")

    def test_repository_may_import_it(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/infrastructure/db/repositories/user_repo.py",
            """
            import sqlalchemy as sa


            class SqlUserRepo:
                async def list_all(self) -> None:
                    sa.select()
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 0


class TestRepositoryPorts:
    """Адаптер знает про колонки — его порт обязан называться репозиторием."""

    def adapter(self, repo: Repo) -> None:
        repo.write("app/infrastructure/db/models/user.py", "class UserORM: ...\n")
        repo.write(
            "app/infrastructure/db/journal.py",
            """
            from app.infrastructure.db.models.user import UserORM


            class SqlWelcomeJournal:
                async def record(self, session) -> None:
                    session.add(UserORM())
            """,
        )

    def test_port_not_named_a_repository(self, repo: Repo) -> None:
        prepare(repo)
        self.adapter(repo)
        repo.write(
            "app/composition/providers/repositories.py",
            """
            from dishka import Provider, provide

            from app.application.ports.welcome_journal import WelcomeJournal
            from app.infrastructure.db.journal import SqlWelcomeJournal


            class RepositoryProvider(Provider):
                journal = provide(SqlWelcomeJournal, provides=WelcomeJournal)
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 1
        assert result.mentions("его порт назван `WelcomeJournal`")

    def test_adapter_given_out_without_a_port(self, repo: Repo) -> None:
        prepare(repo)
        self.adapter(repo)
        repo.write(
            "app/composition/providers/repositories.py",
            """
            from dishka import Provider, provide

            from app.infrastructure.db.journal import SqlWelcomeJournal


            class RepositoryProvider(Provider):
                journal = provide(SqlWelcomeJournal)
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 1
        assert result.mentions("без порта")

    def test_port_named_a_repository_but_living_elsewhere(self, repo: Repo) -> None:
        prepare(repo)
        self.adapter(repo)
        repo.write(
            "app/composition/providers/repositories.py",
            """
            from dishka import Provider, provide

            from app.application.ports.welcome_journal import WelcomeJournalRepo
            from app.infrastructure.db.journal import SqlWelcomeJournal


            class RepositoryProvider(Provider):
                journal = provide(SqlWelcomeJournal, provides=WelcomeJournalRepo)
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 1
        assert result.mentions("порты репозиториев живут в")

    def test_properly_named_port_in_its_place(self, repo: Repo) -> None:
        prepare(repo)
        self.adapter(repo)
        repo.write(
            "app/composition/providers/repositories.py",
            """
            from dishka import Provider, provide

            from app.application.ports.repositories.welcome_journal import WelcomeJournalRepo
            from app.infrastructure.db.journal import SqlWelcomeJournal


            class RepositoryProvider(Provider):
                journal = provide(SqlWelcomeJournal, provides=WelcomeJournalRepo)
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 0

    def test_missing_models_dir_is_a_config_error(self, repo: Repo) -> None:
        repo.pyproject(DB_ACCESS)
        repo.write("app/application/ports/repositories/__init__.py")
        repo.write("app/composition/providers/__init__.py")

        result = repo.run("check_db_access")

        assert result.code == 2
        assert result.mentions("[tool.db_access].orm_models")


class TestRepositoryVariableNames:
    def test_parameter_hiding_its_role(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/use_cases/report.py",
            """
            class ReportUseCase:
                def __init__(self, users: UserRepo) -> None:
                    self._users = users
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 1
        assert result.mentions("роль хранилища видна только в типе")
        assert result.mentions("поле `self._users`")

    def test_named_parameter_and_field_pass(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/use_cases/report.py",
            """
            class ReportUseCase:
                def __init__(self, users_repo: UserRepo) -> None:
                    self._users_repo = users_repo
            """,
        )

        result = repo.run("check_db_access")

        assert result.code == 0
