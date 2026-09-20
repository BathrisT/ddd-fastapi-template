"""Форма класса: один поведенческий на файл, зависимости — в конструкторе.

Носители данных в счёт не идут, и это половина ценности сторожа: тридцать
pydantic-схем в одном файле норма, два сценария — нет. Поэтому тесты про
носителей здесь не менее важны, чем тесты про нарушение.
"""

from tests.unit.scripts.conftest import Repo


class TestOneClassPerFile:
    def test_two_behavioural_classes_in_one_file(self, repo: Repo) -> None:
        repo.write(
            "app/services/mixed.py",
            """
            class Sender:
                def send(self) -> None: ...


            class Receiver:
                def receive(self) -> None: ...
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 1
        assert result.mentions("2 классов с поведением")

    def test_data_carriers_may_share_a_file(self, repo: Repo) -> None:
        repo.write(
            "app/schemas/portal.py",
            """
            from dataclasses import dataclass
            from enum import Enum

            from pydantic import BaseModel


            class PortalIn(BaseModel):
                name: str


            class PortalOut(BaseModel):
                id: int


            class PortalKind(Enum):
                MAIN = "main"


            @dataclass(frozen=True)
            class PortalKey:
                value: str


            class PortalError(Exception): ...
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 0

    def test_carrier_inherited_from_a_carrier_is_still_a_carrier(self, repo: Repo) -> None:
        """`class B(A)`, где `A(BaseModel)`, — форма, описанная полями.

        Смотри правило только на ПРЯМУЮ базу, и пара «завести новое /
        переписать существующее» требовала бы копии всех полей с валидаторами.
        """
        repo.write(
            "app/schemas/args.py",
            """
            from pydantic import BaseModel


            class SurveyArgs(BaseModel):
                title: str


            class SurveyEditArgs(SurveyArgs):
                survey_id: int
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 0


class TestStaticDependencies:
    def test_static_method_taking_a_port(self, repo: Repo) -> None:
        repo.write(
            "app/application/ports/user_repo.py",
            """
            from typing import Protocol


            class UserRepo(Protocol):
                async def get_by_id(self, user_id: int) -> None: ...
            """,
        )
        repo.write(
            "app/domain/services/report.py",
            """
            class Report:
                @staticmethod
                async def build(users_repo: "UserRepo") -> None: ...
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 1
        assert result.mentions("`UserRepo` (порт)")

    def test_static_method_taking_a_secret(self, repo: Repo) -> None:
        repo.write(
            "app/domain/services/signer.py",
            """
            class Signer:
                @staticmethod
                def sign(payload: str, api_key: str) -> str:
                    return payload + api_key
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 1
        assert result.mentions("принимает секрет `api_key`")

    def test_ordinary_method_may_take_a_port(self, repo: Repo) -> None:
        """У обычного метода порт аргументом бывает законен — скоупнутый на запрос."""
        repo.write(
            "app/application/ports/user_repo.py",
            """
            from typing import Protocol


            class UserRepo(Protocol):
                async def get_by_id(self, user_id: int) -> None: ...
            """,
        )
        repo.write(
            "app/domain/services/report.py",
            """
            class Report:
                async def build(self, users_repo: "UserRepo") -> None: ...
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 0


COMPOSITION = """
[tool.composition]
composition_roots = ["app/composition"]
"""

PROVIDER = """
from dishka import Provider, Scope, from_context, provide

from app.application.services.analytics.recorder import EventRecorder
from app.config import Settings


class AppProvider(Provider):
    scope = Scope.APP

    settings = from_context(provides=Settings, scope=Scope.APP)
    users = provide(SqlUserRepo, provides=UserRepo)

    @provide
    def recorder(self, settings: Settings) -> EventRecorder: ...
"""


def with_container(repo: Repo) -> None:
    """Проект с композицией: контейнер умеет собирать четыре типа."""
    repo.pyproject(COMPOSITION)
    repo.write("app/composition/providers/app.py", PROVIDER)


class TestContainerBuiltDependencies:
    def test_service_built_by_container(self, repo: Repo) -> None:
        """Соседский случай: сервис не порт и лежит не в ports/, а внедряется."""
        with_container(repo)
        repo.write(
            "app/interface/api/routes/search.py",
            """
            class SearchEvent:
                @staticmethod
                async def record(recorder: "EventRecorder", query: str) -> None: ...
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 1
        assert result.mentions("EventRecorder")
        assert result.mentions("собирает контейнер")

    def test_implementation_counts_too(self, repo: Repo) -> None:
        """`provide(SqlUserRepo, provides=UserRepo)` объявляет обе стороны."""
        with_container(repo)
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                @staticmethod
                async def build(repo: "SqlUserRepo") -> None: ...
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 1
        assert result.mentions("SqlUserRepo")

    def test_from_context_is_not_forgotten(self, repo: Repo) -> None:
        """`Settings` объявлен формой, которую теряет наивный разбор."""
        with_container(repo)
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                @staticmethod
                async def build(settings: "Settings") -> None: ...
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 1
        assert result.mentions("Settings")

    def test_composition_may_take_dependencies(self, repo: Repo) -> None:
        """Композиция и есть место, где зависимости собирают руками."""
        with_container(repo)
        repo.write(
            "app/composition/container.py",
            """
            class AppContainer:
                @staticmethod
                def build(settings: "Settings") -> None: ...
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 0

    def test_same_type_transformer_is_not_a_consumer(self, repo: Repo) -> None:
        """Отдаёт тот же тип, что принял: зависимость живёт в чужом `__init__`."""
        with_container(repo)
        repo.write(
            "app/infrastructure/db/autonomous_engine.py",
            """
            class AutonomousEngine:
                @staticmethod
                def for_(main: "EventRecorder") -> "EventRecorder": ...
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 0

    def test_unknown_type_is_not_a_dependency(self, repo: Repo) -> None:
        with_container(repo)
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                @staticmethod
                async def build(payload: "SearchOutcome") -> None: ...
            """,
        )

        result = repo.run("check_class_shape")

        assert result.code == 0
