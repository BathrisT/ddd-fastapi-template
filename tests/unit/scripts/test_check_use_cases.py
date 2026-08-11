"""Правила сценариев: один файл — один сценарий, один `commit()` на вход.

Пять требований, и все пять про одно: сколько действий выполняется в одном
запросе. Отдельного внимания стоит строгий режим носителей данных —
`@dataclass` с методом `execute` в каталоге сценариев это не «данные», а
сценарий, спрятанный от проверки.
"""

from tests.unit.scripts.conftest import Repo


def prepare(repo: Repo) -> None:
    repo.write("app/application/use_cases/__init__.py")


class TestScenarioFile:
    def test_single_scenario_passes(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/use_cases/register_user.py",
            """
            from dataclasses import dataclass


            @dataclass(frozen=True)
            class RegisterUserCommand:
                email: str


            class RegisterUserUseCase:
                async def execute(self, command: RegisterUserCommand) -> None: ...
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 0

    def test_second_public_method_is_a_second_scenario(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/use_cases/goal_actions.py",
            """
            class GoalActionsUseCase:
                async def execute(self) -> None: ...

                async def reject(self) -> None: ...
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 1
        assert result.mentions("публичные методы помимо execute")

    def test_private_helper_method_is_fine(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/use_cases/register_user.py",
            """
            class RegisterUserUseCase:
                async def execute(self) -> None:
                    self._normalise()

                def _normalise(self) -> None: ...
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 0

    def test_stray_helper_class_is_rejected(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/use_cases/goal_validation.py",
            """
            class GoalValidation:
                def validate(self) -> None: ...
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 1
        assert result.mentions("не сценарий")

    def test_two_scenarios_in_one_file(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/use_cases/users.py",
            """
            class RegisterUserUseCase:
                async def execute(self) -> None: ...


            class WelcomeUserUseCase:
                async def execute(self) -> None: ...
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 1
        assert result.mentions("2 сценария в одном файле")

    def test_dataclass_with_behaviour_does_not_hide_a_scenario(self, repo: Repo) -> None:
        """Строгий режим: в каталоге сценариев носителей с поведением не бывает.

        Без него три сценария подряд лежали под `@dataclass` и не проверялись
        вообще ничем.
        """
        prepare(repo)
        repo.write(
            "app/application/use_cases/setup.py",
            """
            from dataclasses import dataclass


            @dataclass
            class SetupStages:
                stages: int

                def run(self) -> None: ...
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 1
        assert result.mentions("не сценарий")

    def test_scenario_without_execute(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/use_cases/empty.py",
            """
            class EmptyUseCase:
                def _prepare(self) -> None: ...
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 1
        assert result.mentions("нет `execute`")

    def test_missing_use_cases_dir_is_a_config_error(self, repo: Repo) -> None:
        """Каталог назван в конфиге — значит его наличие часть настройки."""
        result = repo.run("check_use_cases")

        assert result.code == 2
        assert result.mentions("[tool.code_layout].use_cases_dir")


class TestOneCommitPerEntry:
    def test_holder_of_a_scenario_must_not_commit(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/interface/worker/dispatcher.py",
            """
            class Dispatcher:
                def __init__(self, register: "RegisterUserUseCase", committer: "Committer") -> None:
                    self._register = register
                    self._committer = committer

                async def handle(self) -> None:
                    await self._register.execute()
                    await self._committer.commit()
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 1
        assert result.mentions("и коммитит сам")

    def test_dispatcher_without_its_own_commit_is_legal(self, repo: Repo) -> None:
        """Держать чужой сценарий законно — запрещено фиксировать за него."""
        prepare(repo)
        repo.write(
            "app/interface/worker/dispatcher.py",
            """
            class Dispatcher:
                def __init__(self, register: "RegisterUserUseCase") -> None:
                    self._register = register

                async def handle(self) -> None:
                    await self._register.execute()
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 0

    def test_entry_asking_for_two_scenarios(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/interface/api/routes/users.py",
            """
            async def register(
                register_user: FromDishka[RegisterUserUseCase],
                welcome_user: FromDishka[WelcomeUserUseCase],
            ) -> None: ...
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 1
        assert result.mentions("просит 2 сценария")

    def test_entry_asking_for_one_scenario(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/interface/api/routes/users.py",
            """
            async def register(register_user: FromDishka[RegisterUserUseCase]) -> None: ...
            """,
        )

        result = repo.run("check_use_cases")

        assert result.code == 0
