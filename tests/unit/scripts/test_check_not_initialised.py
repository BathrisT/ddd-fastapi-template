"""Клон шаблона, не отвязанный от шаблона.

Отличить шаблон от свежего клона по дереву невозможно — они байт в байт
одинаковы, — поэтому признак «здесь дорабатывают сам шаблон» живёт вне git.
Тесты закрывают обе стороны: клон обязан упереться в отказ, шаблон — нет.
"""

from tests.unit.scripts.conftest import Repo

TEMPLATE_SECTION = """
[tool.template]
url = "https://example.com/template.git"
name = "fixture-project"
"""


class TestNotInitialised:
    def test_project_name_equal_to_template_name(self, repo: Repo) -> None:
        repo.pyproject(TEMPLATE_SECTION)

        result = repo.run("check_not_initialised")

        assert result.code == 1
        assert result.mentions("не отвязан от шаблона")
        assert result.mentions("make init")

    def test_renamed_project_passes(self, repo: Repo) -> None:
        repo.pyproject('[tool.template]\nname = "some-other-template"\n')

        result = repo.run("check_not_initialised")

        assert result.code == 0

    def test_local_marker_means_this_is_the_template_itself(self, repo: Repo) -> None:
        """Маркер локальный и заведён руками: закоммиченный приехал бы в клон
        и молчал бы ровно там, где нужен голос."""
        repo.pyproject(TEMPLATE_SECTION)
        repo.write(".is-template")

        result = repo.run("check_not_initialised")

        assert result.code == 0

    def test_project_not_from_a_template(self, repo: Repo) -> None:
        result = repo.run("check_not_initialised")

        assert result.code == 0

    def test_pep_621_name_is_read_too(self, repo: Repo) -> None:
        """Проект, переехавший на `[project]`, не должен молча перестать проверяться."""
        repo.write(
            "pyproject.toml",
            """
            [project]
            name = "fixture-project"

            [tool.code_layout]
            source_root = "app"

            [tool.template]
            name = "fixture-project"
            """,
        )

        result = repo.run("check_not_initialised")

        assert result.code == 1
        assert result.mentions("не отвязан от шаблона")
