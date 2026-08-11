"""Функции уровня модуля в `app/`: логика принадлежит классу.

Главное здесь — не «ловится ли `def` на верхнем уровне» (это ловит и греп), а
две вещи, на которых сторож молчал бы незаметно: `def` под условием и белый
список путей, заданный конфигом.
"""

from tests.unit.scripts.conftest import Repo


class TestModuleFunctions:
    def test_module_level_function_is_rejected(self, repo: Repo) -> None:
        repo.write(
            "app/service.py",
            """
            def normalise(value: str) -> str:
                return value.strip()
            """,
        )

        result = repo.run("check_module_functions")

        assert result.code == 1
        assert result.mentions("app/service.py:1: функция уровня модуля `normalise`")

    def test_method_of_a_class_is_fine(self, repo: Repo) -> None:
        repo.write(
            "app/service.py",
            """
            class Service:
                @staticmethod
                def normalise(value: str) -> str:
                    return value.strip()
            """,
        )

        result = repo.run("check_module_functions")

        assert result.code == 0

    def test_function_under_a_condition_is_still_module_level(self, repo: Repo) -> None:
        """`if TYPE_CHECKING:` — те же ворота, только на строку глубже.

        Обход одного `tree.body` оставлял их открытыми, а знают о них все.
        """
        repo.write(
            "app/service.py",
            """
            from typing import TYPE_CHECKING

            if TYPE_CHECKING:
                def normalise(value: str) -> str: ...
            """,
        )

        result = repo.run("check_module_functions")

        assert result.code == 1
        assert result.mentions("`normalise`")

    def test_function_inside_try_is_still_module_level(self, repo: Repo) -> None:
        repo.write(
            "app/service.py",
            """
            try:
                from fast import normalise
            except ImportError:
                def normalise(value: str) -> str:
                    return value
            """,
        )

        result = repo.run("check_module_functions")

        assert result.code == 1

    def test_nested_function_is_not_module_level(self, repo: Repo) -> None:
        repo.write(
            "app/service.py",
            """
            class Service:
                def run(self) -> None:
                    def inner() -> None: ...

                    inner()
            """,
        )

        result = repo.run("check_module_functions")

        assert result.code == 0

    def test_allowed_prefix_lets_the_framework_have_its_names(self, repo: Repo) -> None:
        """Исключение заводится ТОЛЬКО правкой конфига — она видна в ревью."""
        repo.pyproject(
            """
            [tool.code_layout]
            source_root = "app"
            module_functions_allowed = ["app/interface"]
            """
        )
        repo.write(
            "app/interface/routes.py",
            """
            async def list_users() -> list[str]:
                return []
            """,
        )

        result = repo.run("check_module_functions")

        assert result.code == 0

    def test_allowed_prefix_does_not_leak_to_a_sibling(self, repo: Repo) -> None:
        """`app/interface` не должен открывать `app/interface_helpers`."""
        repo.pyproject(
            """
            [tool.code_layout]
            source_root = "app"
            module_functions_allowed = ["app/interface"]
            """
        )
        repo.write(
            "app/interface_helpers/tools.py",
            """
            def helper() -> None: ...
            """,
        )

        result = repo.run("check_module_functions")

        assert result.code == 1
