"""Длина файла: порог берётся из конфига, а не зашит в скрипт."""

from tests.unit.scripts.conftest import Repo

LIMIT = """
[tool.code_layout]
source_root = "app"
max_lines = 5
"""


class TestFileLength:
    def test_file_over_the_limit_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(LIMIT)
        repo.write("app/swamp.py", "x = 1\n" * 6)

        result = repo.run("check_file_length")

        assert result.code == 1
        assert result.mentions("app/swamp.py: 6 строк при лимите 5")

    def test_file_at_the_limit_passes(self, repo: Repo) -> None:
        repo.pyproject(LIMIT)
        repo.write("app/tidy.py", "x = 1\n" * 5)

        result = repo.run("check_file_length")

        assert result.code == 0

    def test_limit_comes_from_the_config(self, repo: Repo) -> None:
        """Тот же файл при поднятом пороге проходит — значение читается, а не зашито."""
        repo.pyproject('[tool.code_layout]\nsource_root = "app"\nmax_lines = 100\n')
        repo.write("app/swamp.py", "x = 1\n" * 6)

        result = repo.run("check_file_length")

        assert result.code == 0
