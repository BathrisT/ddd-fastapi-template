"""Одно значение, разложенное константами по N файлам, — размноженное правило.

Сторож ловит не смысл, а симптом, поэтому важнее всего проверить, что он НЕ
срабатывает на совпадениях, которые ничего не значат: одно и то же число под
несвязанными именами и вырожденные значения. Проверка, которая ругается зря,
перестаёт читаться целиком — вместе с настоящими находками.
"""

from tests.unit.scripts.conftest import Repo

THRESHOLD = """
[tool.code_layout]
source_root = "app"
duplicated_constant_files = 3
"""


class TestDuplicatedConstants:
    def test_same_rule_copied_across_files_is_reported(self, repo: Repo) -> None:
        repo.pyproject(THRESHOLD)
        for name in ("report", "survey", "digest"):
            repo.write(f"app/tasks/{name}.py", "SEND_HOUR = 9\n")

        result = repo.run("check_duplicated_constants")

        assert result.code == 1
        assert result.mentions("задано константой в 3 файлах")

    def test_names_related_only_by_value_are_not_a_rule(self, repo: Repo) -> None:
        """`9` — это и час рассылки, и лимит, и номер класса.

        Признак СКОПИРОВАННОГО правила — общее слово в именах; без него
        находка ничего не подсказывает.
        """
        repo.pyproject(THRESHOLD)
        repo.write("app/tasks/alpha.py", "MAX_RETRIES = 9\n")
        repo.write("app/tasks/beta.py", "GRADE_LEVEL = 9\n")
        repo.write("app/tasks/gamma.py", "BLOCK_SIZE = 9\n")

        result = repo.run("check_duplicated_constants")

        assert result.code == 0

    def test_trivial_values_are_ignored(self, repo: Repo) -> None:
        repo.pyproject(THRESHOLD)
        for name in ("a", "b", "c"):
            repo.write(f"app/tasks/{name}.py", "DEFAULT_LIMIT = 1\n")

        result = repo.run("check_duplicated_constants")

        assert result.code == 0

    def test_class_level_constants_count_too(self, repo: Repo) -> None:
        """Правило раскладки выдавливает код в классы — вместе с константами.

        Смотри сторож только на тело модуля, он ослеп бы ровно там, куда
        соседнее правило всё и переносит.
        """
        repo.pyproject(THRESHOLD)
        for name in ("report", "survey", "digest"):
            repo.write(
                f"app/tasks/{name}.py",
                """
                class Schedule:
                    SEND_HOUR = 9
                """,
            )

        result = repo.run("check_duplicated_constants")

        assert result.code == 1
        assert result.mentions("SEND_HOUR")

    def test_under_the_threshold_stays_quiet(self, repo: Repo) -> None:
        repo.pyproject(THRESHOLD)
        for name in ("report", "survey"):
            repo.write(f"app/tasks/{name}.py", "SEND_HOUR = 9\n")

        result = repo.run("check_duplicated_constants")

        assert result.code == 0
