"""Реализация называет свой порт вслух — и реализует его целиком.

Сторож заведён на дыру, которой не видит ни одна другая проверка: dishka
принимает обе стороны биндинга как `Any`, а объект создаёт по отражению — и
`AttributeError` от расхождения с портом наступает в проде.

Отсюда главный класс тестов здесь: не только «ловит ли», но и «не зеленеет ли
молча» — на чужом классе, на ненастроенной секции, на порте под примесью.
"""

from tests.unit.scripts.conftest import Repo

CONFIG = """
[tool.port_inheritance]
ports_root = "app/application/ports"
provider_dirs = ["app/composition/providers"]
exempt = []
"""

PORT = """
from typing import Protocol


class UserRepo(Protocol):
    async def save(self, user: object) -> object: ...

    async def get_by_id(self, user_id: int) -> object | None: ...
"""


def prepare(repo: Repo, port: str = PORT) -> None:
    repo.pyproject(CONFIG)
    repo.write("app/application/ports/user_repo.py", port)
    repo.mkdir("app/composition/providers")


def bind(repo: Repo, body: str) -> None:
    repo.write("app/composition/providers/repositories.py", body)


WHOLE_IMPL = """
from app.application.ports.user_repo import UserRepo


class SqlUserRepo(UserRepo):
    async def save(self, user: object) -> object:
        return user

    async def get_by_id(self, user_id: int) -> object | None:
        return None
"""


class TestSilentPort:
    def test_implementation_not_naming_its_port_is_rejected(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/infrastructure/user_repo.py",
            """
            class SqlUserRepo:
                async def save(self, user: object) -> object:
                    return user

                async def get_by_id(self, user_id: int) -> object | None:
                    return None
            """,
        )
        bind(
            repo,
            """
            from dishka import Provider, provide

            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.user_repo import SqlUserRepo


            class RepositoryProvider(Provider):
                users = provide(SqlUserRepo, provides=UserRepo)
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 1
        assert result.mentions("порт свой не называет")

    def test_naming_the_port_passes(self, repo: Repo) -> None:
        prepare(repo)
        repo.write("app/infrastructure/user_repo.py", WHOLE_IMPL)
        bind(
            repo,
            """
            from dishka import Provider, provide

            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.user_repo import SqlUserRepo


            class RepositoryProvider(Provider):
                users = provide(SqlUserRepo, provides=UserRepo)
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 0


class TestMissingMethod:
    """Забытый метод наследуется телом `...`: не падает, а тихо отдаёт `None`."""

    def test_missing_method_is_rejected(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/infrastructure/user_repo.py",
            """
            from app.application.ports.user_repo import UserRepo


            class SqlUserRepo(UserRepo):
                async def save(self, user: object) -> object:
                    return user
            """,
        )
        bind(
            repo,
            """
            from dishka import Provider, provide

            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.user_repo import SqlUserRepo


            class RepositoryProvider(Provider):
                users = provide(SqlUserRepo, provides=UserRepo)
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 1
        assert result.mentions("не реализует")
        assert result.mentions("get_by_id")

    def test_method_inherited_from_a_mixin_counts(self, repo: Repo) -> None:
        """Порт бывает под примесью, а метод — в ней: оба пути идут вверх."""
        prepare(repo)
        repo.write(
            "app/infrastructure/reads.py",
            """
            class ReadsUsers:
                async def get_by_id(self, user_id: int) -> object | None:
                    return None
            """,
        )
        repo.write(
            "app/infrastructure/user_repo.py",
            """
            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.reads import ReadsUsers


            class SqlUserRepo(ReadsUsers, UserRepo):
                async def save(self, user: object) -> object:
                    return user
            """,
        )
        bind(
            repo,
            """
            from dishka import Provider, provide

            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.user_repo import SqlUserRepo


            class RepositoryProvider(Provider):
                users = provide(SqlUserRepo, provides=UserRepo)
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 0

    def test_method_of_a_sibling_port_does_not_count(self, repo: Repo) -> None:
        """Класс под двумя портами: метод одного не реализует другой.

        Совпадение имён у двух портов — и одноимённый метод соседа выглядел бы
        унаследованным по-честному, а забытая реализация проходила бы молча.
        """
        prepare(repo)
        repo.write(
            "app/application/ports/job_results.py",
            """
            from typing import Protocol


            class JobResults(Protocol):
                async def get_by_id(self, user_id: int) -> object | None: ...
            """,
        )
        repo.write(
            "app/infrastructure/user_repo.py",
            """
            from app.application.ports.job_results import JobResults
            from app.application.ports.user_repo import UserRepo


            class SqlUserRepo(UserRepo, JobResults):
                async def save(self, user: object) -> object:
                    return user
            """,
        )
        bind(
            repo,
            """
            from dishka import Provider, provide

            from app.application.ports.job_results import JobResults
            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.user_repo import SqlUserRepo


            class RepositoryProvider(Provider):
                users = provide(SqlUserRepo, provides=UserRepo)
                jobs = provide(SqlUserRepo, provides=JobResults)
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 1
        assert result.mentions("get_by_id")


class TestFactoryForms:
    """Пару называет не только `provide(X, provides=P)`."""

    def test_constructed_inside_a_factory_is_checked(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/infrastructure/user_repo.py",
            """
            class SqlUserRepo:
                async def save(self, user: object) -> object:
                    return user
            """,
        )
        bind(
            repo,
            """
            from dishka import Provider, provide

            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.user_repo import SqlUserRepo


            class RepositoryProvider(Provider):
                @provide
                def users(self, session: object) -> UserRepo:
                    return SqlUserRepo()
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 1
        assert result.mentions("порт свой не называет")

    def test_alias_passing_an_argument_through_is_checked(self, repo: Repo) -> None:
        """Один объект под двумя портами: `return client` — тоже биндинг."""
        prepare(repo)
        repo.write(
            "app/infrastructure/user_repo.py",
            """
            class SqlUserRepo:
                async def save(self, user: object) -> object:
                    return user
            """,
        )
        bind(
            repo,
            """
            from dishka import Provider, provide

            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.user_repo import SqlUserRepo


            class RepositoryProvider(Provider):
                @provide
                def users(self, client: SqlUserRepo) -> UserRepo:
                    return client
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 1
        assert result.mentions("порт свой не называет")

    def test_resource_wrapper_is_unwrapped(self, repo: Repo) -> None:
        """`AsyncIterator[UserRepo]` — это порт, а не обёртка ресурса."""
        prepare(repo)
        repo.write(
            "app/infrastructure/user_repo.py",
            """
            class SqlUserRepo:
                async def save(self, user: object) -> object:
                    return user
            """,
        )
        bind(
            repo,
            """
            from collections.abc import AsyncIterator

            from dishka import Provider, provide

            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.user_repo import SqlUserRepo


            class RepositoryProvider(Provider):
                @provide
                async def users(self) -> AsyncIterator[UserRepo]:
                    yield SqlUserRepo()
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 1
        assert result.mentions("порт свой не называет")


class TestOutsideTheRule:
    def test_undecorated_helper_is_not_a_binding(self, repo: Repo) -> None:
        """Биндинг объявляет декоратор, а не аннотация возврата.

        Приватный хелпер рядом с провайдером иначе считался бы фабрикой, и
        сторож падал бы на коде, к графу зависимостей не относящемся.
        """
        prepare(repo)
        repo.write(
            "app/infrastructure/user_repo.py",
            """
            class SqlUserRepo:
                async def save(self, user: object) -> object:
                    return user
            """,
        )
        bind(
            repo,
            """
            from dishka import Provider

            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.user_repo import SqlUserRepo


            class RepositoryProvider(Provider):
                def _make(self) -> UserRepo:
                    return SqlUserRepo()
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 0

    def test_foreign_class_is_left_alone(self, repo: Repo) -> None:
        """Чужой класс правилу не подчиняется: дописать `(UserRepo)` некуда."""
        prepare(repo)
        bind(
            repo,
            """
            from dishka import Provider, provide
            from vendor.sdk import VendorUserRepo

            from app.application.ports.user_repo import UserRepo


            class RepositoryProvider(Provider):
                users = provide(VendorUserRepo, provides=UserRepo)
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 0

    def test_binding_without_a_port_is_not_a_port_binding(self, repo: Repo) -> None:
        """`provide(RegisterUserUseCase)` — сценарий сам себе тип, порта нет."""
        prepare(repo)
        repo.write(
            "app/application/use_cases/register_user.py",
            """
            class RegisterUserUseCase:
                async def execute(self) -> None:
                    return None
            """,
        )
        bind(
            repo,
            """
            from dishka import Provider, provide

            from app.application.use_cases.register_user import RegisterUserUseCase


            class UseCaseProvider(Provider):
                register_user = provide(RegisterUserUseCase)
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 0

    def test_type_outside_the_ports_directory_is_not_a_port(self, repo: Repo) -> None:
        """Место решает, а не имя: сервис из `application/services/` — не порт."""
        prepare(repo)
        repo.write(
            "app/application/services/caller_resolver.py",
            """
            class CallerResolver:
                def resolve(self) -> None:
                    return None
            """,
        )
        bind(
            repo,
            """
            from dishka import Provider, provide

            from app.application.services.caller_resolver import CallerResolver


            class ProcessProvider(Provider):
                @provide
                def caller_resolver(self) -> CallerResolver:
                    return CallerResolver()
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 0

    def test_exempt_implementation_is_skipped(self, repo: Repo) -> None:
        prepare(repo)
        repo.pyproject(CONFIG.replace("exempt = []", 'exempt = ["SqlUserRepo"]'))
        repo.write("app/application/ports/user_repo.py", PORT)
        repo.write(
            "app/infrastructure/user_repo.py",
            """
            class SqlUserRepo:
                async def save(self, user: object) -> object:
                    return user
            """,
        )
        bind(
            repo,
            """
            from dishka import Provider, provide

            from app.application.ports.user_repo import UserRepo
            from app.infrastructure.user_repo import SqlUserRepo


            class RepositoryProvider(Provider):
                users = provide(SqlUserRepo, provides=UserRepo)
            """,
        )

        result = repo.run("check_port_inheritance")

        assert result.code == 0


class TestConfiguration:
    def test_missing_section_is_announced_not_silently_green(self, repo: Repo) -> None:
        repo.pyproject()

        result = repo.run("check_port_inheritance")

        assert result.code == 0
        assert result.mentions("не настроен")

    def test_typo_in_a_path_is_a_configuration_error(self, repo: Repo) -> None:
        """Опечатка в пути обнуляла бы обход — и сторож печатал бы «OK»."""
        repo.pyproject(CONFIG.replace("app/application/ports", "app/application/portz"))
        repo.mkdir("app/composition/providers")

        result = repo.run("check_port_inheritance")

        assert result.code == 2
        assert result.mentions("ОШИБКА НАСТРОЙКИ")
