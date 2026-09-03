"""Бюджет прозы: три лимита, и каждый читает свой порог из конфига."""

from tests.unit.scripts.conftest import Repo

CONFIG = """
[tool.comment_budget]
roots = ["app"]
max_run = 4
max_module_docstring = 5
max_ratio = 0.4
min_code_lines = 3
free_prose_lines = 0
"""


def code(lines: int) -> str:
    return "".join(f"value_{number} = {number}\n" for number in range(lines))


class TestCommentRun:
    def test_run_over_the_limit_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        repo.write("app/service.py", code(20) + "# один\n" * 5)

        result = repo.run("check_comment_budget")

        assert result.code == 1
        assert result.mentions("5 строк прозы подряд при лимите 4")

    def test_run_at_the_limit_passes(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        repo.write("app/service.py", code(20) + "# один\n" * 4)

        result = repo.run("check_comment_budget")

        assert result.code == 0

    def test_blank_line_does_not_break_the_run(self, repo: Repo) -> None:
        """Иначе лимит обходится пробелом через каждые четыре строки."""
        repo.pyproject(CONFIG)
        repo.write("app/service.py", code(20) + "# один\n" * 3 + "\n" + "# один\n" * 3)

        result = repo.run("check_comment_budget")

        assert result.code == 1
        assert result.mentions("6 строк прозы подряд")

    def test_code_between_comments_breaks_the_run(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        repo.write("app/service.py", code(20) + ("# один\n" * 3 + "x = 1\n") * 3)

        result = repo.run("check_comment_budget")

        assert result.code == 0

    def test_docstring_counts_as_prose(self, repo: Repo) -> None:
        """Иначе правило обходится тройными кавычками за секунду."""
        repo.pyproject(CONFIG)
        body = '    """строка\n' + "    строка\n" * 4 + '    """\n'
        repo.write("app/service.py", code(20) + "def handler():\n" + body + "    return 1\n")

        result = repo.run("check_comment_budget")

        assert result.code == 1
        assert result.mentions("прозы подряд")


class TestModuleHeader:
    def test_header_has_its_own_budget(self, repo: Repo) -> None:
        """Пять строк текста в шапке проходят, хотя прогон в теле ограничен четырьмя."""
        repo.pyproject(CONFIG)
        repo.write("app/service.py", '"""один\n' + "два\n" * 3 + '"""\n' + code(20))

        result = repo.run("check_comment_budget")

        assert result.code == 0

    def test_header_over_its_budget_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        repo.write("app/service.py", '"""один\n' + "два\n" * 5 + '"""\n' + code(20))

        result = repo.run("check_comment_budget")

        assert result.code == 1
        assert result.mentions("шапка модуля 6 строк при бюджете 5")


class TestRatio:
    def test_file_over_the_ratio_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        repo.write("app/service.py", ("# один\nx = 1\n" * 10))

        result = repo.run("check_comment_budget")

        assert result.code == 1
        assert result.mentions("прозы 10 строк на 10 строк кода (бюджет 4)")

    def test_short_file_skips_the_ratio(self, repo: Repo) -> None:
        """`__init__.py` из одной строки с докстрокой — это 100% и это норма."""
        repo.pyproject(CONFIG)
        repo.write("app/service.py", '"""Один.\n\nДва.\n"""\nx = 1\n')

        result = repo.run("check_comment_budget")

        assert result.code == 0

    def test_small_file_gets_a_free_allowance(self, repo: Repo) -> None:
        """В файле на 23 строки кода 40% — это 9 строк, куда не влезут шапка и докстроки."""
        repo.pyproject(CONFIG.replace("free_prose_lines = 0", "free_prose_lines = 12"))
        repo.write("app/service.py", code(20) + ("# один\n" * 4 + "x = 1\n") * 4)

        result = repo.run("check_comment_budget")

        assert result.code == 1
        assert result.mentions("бюджет 12")

    def test_multiline_string_value_is_code_not_prose(self, repo: Repo) -> None:
        """SQL в переменной — это код, сколько бы строк он ни занимал."""
        repo.pyproject(CONFIG)
        repo.write("app/service.py", 'QUERY = """\n' + "select 1\n" * 20 + '"""\n')

        result = repo.run("check_comment_budget")

        assert result.code == 0


class TestConfig:
    def test_thresholds_come_from_the_config(self, repo: Repo) -> None:
        """Тот же файл при поднятых порогах проходит — значения читаются, а не зашиты."""
        repo.pyproject(
            '[tool.comment_budget]\nroots = ["app"]\nmax_run = 50\n'
            "max_module_docstring = 50\nmax_ratio = 5.0\nfree_prose_lines = 0\n"
        )
        repo.write("app/service.py", code(20) + "# один\n" * 30)

        result = repo.run("check_comment_budget")

        assert result.code == 0

    def test_missing_root_is_a_loud_failure(self, repo: Repo) -> None:
        repo.pyproject('[tool.comment_budget]\nroots = ["nowhere"]\n')

        result = repo.run("check_comment_budget")

        assert result.code == 2
        assert result.mentions("ОШИБКА НАСТРОЙКИ")

    def test_broken_syntax_is_not_our_business(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        repo.write("app/broken.py", "def (:\n")

        result = repo.run("check_comment_budget")

        assert result.code == 0
