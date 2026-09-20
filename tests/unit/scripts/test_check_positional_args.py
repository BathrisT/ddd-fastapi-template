"""Аргументов четыре и больше — называть по имени все.

Сторож заведён на ребус на месте вызова: четыре значения подряд читаются только
вместе с открытым рядом определением, а перепутанные местами соседи одного типа
не дают ни ошибки, ни красного теста.

Отсюда два класса тестов: ловит ли он живые формы вызова (зависимость из
контейнера, статический метод, своё поле) — и не зеленеет ли молча там, где имя
дать нельзя вовсе.
"""

from tests.unit.scripts.conftest import Repo

CONFIG = """
[tool.positional_args]
roots = ["app"]
names_required_from = 4
exempt = []
"""

PORT = """
from typing import Protocol


class Reporter(Protocol):
    async def report(
        self, origin: object, query: str, outcome: object, total_ms: int
    ) -> None: ...
"""


def prepare(repo: Repo) -> None:
    repo.pyproject(CONFIG)
    repo.write("app/application/ports/reporter.py", PORT)


class TestLiveCallForms:
    def test_dependency_from_the_container_is_caught(self, repo: Repo) -> None:
        """Самая частая форма: зовут то, что пришло из контейнера."""
        prepare(repo)
        repo.write(
            "app/interface/api/routes/search.py",
            """
            from app.application.ports.reporter import Reporter


            async def search(reporter: Reporter, request: object, q: str) -> None:
                await reporter.report(request, q, None, 12)
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 1
        assert result.mentions("reporter.report")

    def test_static_method_with_one_named_argument_is_caught(self, repo: Repo) -> None:
        """Четыре аргумента, три без имени — порог считается по всем."""
        prepare(repo)
        repo.write(
            "app/application/services/builder.py",
            """
            class SearchResponseBuilder:
                @staticmethod
                def response(outcome: object, query: str, total_ms: int, debug: bool) -> dict:
                    return {}
            """,
        )
        repo.write(
            "app/interface/api/routes/search.py",
            """
            from app.application.services.builder import SearchResponseBuilder


            async def search(outcome: object, q: str, total_ms: int, debug: int) -> dict:
                return SearchResponseBuilder.response(outcome, q, total_ms, debug=bool(debug))
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 1
        assert result.mentions("SearchResponseBuilder.response")
        assert result.mentions("аргументов 4")

    def test_own_method_is_caught(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/services/pricing.py",
            """
            class Pricing:
                def _apply(self, base: int, tax: int, discount: int, rounding: int) -> int:
                    return base

                def total(self) -> int:
                    return self._apply(100, 20, 5, 2)
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 1
        assert result.mentions("self._apply")

    def test_own_field_is_caught(self, repo: Repo) -> None:
        """Тип поля берётся из конструктора — так же, как у соседних сторожей."""
        prepare(repo)
        repo.write(
            "app/application/use_cases/search.py",
            """
            from app.application.ports.reporter import Reporter


            class SearchUseCase:
                def __init__(self, reporter: Reporter) -> None:
                    self._reporter = reporter

                async def execute(self, request: object, q: str) -> None:
                    await self._reporter.report(request, q, None, 12)
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 1
        assert result.mentions("self._reporter.report")

    def test_constructor_of_a_data_carrier_is_caught(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/domain/models/attempt.py",
            """
            from dataclasses import dataclass


            @dataclass
            class Attempt:
                user_id: int
                tries: int
                confirmed: bool
                notified: bool
            """,
        )
        repo.write(
            "app/application/services/maker.py",
            """
            from app.domain.models.attempt import Attempt


            class Maker:
                def make(self) -> Attempt:
                    return Attempt(1, 3, True, False)
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 1
        assert result.mentions("Attempt")


class TestNamesAreEnough:
    def test_every_argument_named_passes(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/interface/api/routes/search.py",
            """
            from app.application.ports.reporter import Reporter


            async def search(reporter: Reporter, request: object, q: str) -> None:
                await reporter.report(origin=request, query=q, outcome=None, total_ms=12)
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 0

    def test_three_arguments_stay_below_the_threshold(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/services/pricing.py",
            """
            class Pricing:
                def _apply(self, base: int, tax: int, discount: int) -> int:
                    return base

                def total(self) -> int:
                    return self._apply(100, 20, 5)
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 0


class TestNothingToName:
    def test_foreign_callable_is_left_alone(self, repo: Repo) -> None:
        """`logger.info("{} {} {} {}", a, b, c, d)` — имени там нет вовсе."""
        prepare(repo)
        repo.write(
            "app/application/use_cases/purge.py",
            """
            from loguru import logger


            class PurgeUseCase:
                async def execute(self) -> None:
                    logger.info("{} {} {} {}", 1, 2, 3, 4)
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 0

    def test_star_args_cannot_be_named(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/services/joiner.py",
            """
            class Joiner:
                def _all(self, *parts: str) -> str:
                    return "".join(parts)

                def run(self) -> str:
                    return self._all("a", "b", "c", "d")
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 0

    def test_positional_only_params_cannot_be_named(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/services/geometry.py",
            """
            class Geometry:
                def _at(self, x: int, y: int, z: int, w: int, /) -> int:
                    return x

                def run(self) -> int:
                    return self._at(1, 2, 3, 4)
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 0

    def test_unpacking_hides_the_count(self, repo: Repo) -> None:
        """`f(*values)` — число аргументов на статике неизвестно."""
        prepare(repo)
        repo.write(
            "app/application/services/pricing.py",
            """
            class Pricing:
                def _apply(self, base: int, tax: int, discount: int, rounding: int) -> int:
                    return base

                def total(self, values: list[int]) -> int:
                    return self._apply(*values)
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 0

    def test_tests_are_not_scanned(self, repo: Repo) -> None:
        """Корни заданы конфигом, и `tests/` в них нет намеренно."""
        prepare(repo)
        repo.write(
            "tests/unit/test_pricing.py",
            """
            from app.application.services.pricing import Pricing


            def test_total() -> None:
                assert Pricing()._apply(100, 20, 5, 2) == 100
            """,
        )
        repo.write(
            "app/application/services/pricing.py",
            """
            class Pricing:
                def _apply(self, base: int, tax: int, discount: int, rounding: int) -> int:
                    return base
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 0


class TestConfiguration:
    def test_threshold_comes_from_the_config(self, repo: Repo) -> None:
        """Ручка обязана крутиться: порог задан в pyproject, а не в скрипте."""
        repo.pyproject(CONFIG.replace("names_required_from = 4", "names_required_from = 3"))
        repo.write(
            "app/application/services/pricing.py",
            """
            class Pricing:
                def _apply(self, base: int, tax: int, discount: int) -> int:
                    return base

                def total(self) -> int:
                    return self._apply(100, 20, 5)
            """,
        )

        result = repo.run("check_positional_args")

        assert result.code == 1
        assert result.mentions("аргументов 3")

    def test_missing_section_is_announced_not_silently_green(self, repo: Repo) -> None:
        repo.pyproject()

        result = repo.run("check_positional_args")

        assert result.code == 0
        assert result.mentions("не настроен")

    def test_typo_in_a_root_is_a_configuration_error(self, repo: Repo) -> None:
        repo.pyproject(CONFIG.replace('roots = ["app"]', 'roots = ["applicaton"]'))

        result = repo.run("check_positional_args")

        assert result.code == 2
        assert result.mentions("ОШИБКА НАСТРОЙКИ")
