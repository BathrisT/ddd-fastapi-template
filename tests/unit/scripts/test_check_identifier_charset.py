"""Латиница в именах: проверяется синтаксис, а не текст комментариев."""

from tests.unit.scripts.conftest import Repo

ROOTS = """
[tool.identifier_charset]
roots = ["app"]
"""


class TestIdentifierCharset:
    def test_russian_variable_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(ROOTS)
        repo.write("app/service.py", "отчёт = 1\n")

        result = repo.run("check_identifier_charset")

        assert result.code == 1
        assert result.mentions("app/service.py:1: имя `отчёт` не на латинице")

    def test_russian_function_and_argument_are_rejected(self, repo: Repo) -> None:
        repo.pyproject(ROOTS)
        repo.write("app/service.py", "def считать(строка):\n    return строка\n")

        result = repo.run("check_identifier_charset")

        assert result.code == 1
        assert result.mentions("имя `считать`")
        assert result.mentions("имя `строка`")

    def test_russian_comments_and_docstrings_pass(self, repo: Repo) -> None:
        """Текст для человека правило не трогает — иначе полпроекта красное."""
        repo.pyproject(ROOTS)
        repo.write(
            "app/service.py",
            '''
            """Считает отчёт по кандидатам."""

            # Здесь живёт правило: пустое имя не проходит.
            report = "отчёт за месяц"
            ''',
        )

        result = repo.run("check_identifier_charset")

        assert result.code == 0

    def test_latin_lookalike_inside_a_word_is_caught(self, repo: Repo) -> None:
        """`Sуnc` с кириллической `у` глазами не отличить от `Sync`."""
        repo.pyproject(ROOTS)
        repo.write("app/service.py", "class SуncService:\n    pass\n")

        result = repo.run("check_identifier_charset")

        assert result.code == 1
        assert result.mentions("SуncService")

    def test_russian_file_name_is_rejected(self, repo: Repo) -> None:
        """Имя модуля — тоже идентификатор: его пишут в `import`."""
        repo.pyproject(ROOTS)
        repo.write("app/отчёт.py", "value = 1\n")

        result = repo.run("check_identifier_charset")

        assert result.code == 1
        assert result.mentions("не на латинице")

    def test_broken_syntax_is_not_our_business(self, repo: Repo) -> None:
        repo.pyproject(ROOTS)
        repo.write("app/broken.py", "def (:\n")

        result = repo.run("check_identifier_charset")

        assert result.code == 0

    def test_missing_root_is_a_loud_failure(self, repo: Repo) -> None:
        """Каталога нет — отказ настройки, а не бодрое «OK» по пустому скану."""
        repo.pyproject('[tool.identifier_charset]\nroots = ["nowhere"]\n')

        result = repo.run("check_identifier_charset")

        assert result.code == 2
        assert result.mentions("ОШИБКА НАСТРОЙКИ")

    def test_roots_come_from_the_config(self, repo: Repo) -> None:
        """Тот же файл вне названных корней не смотрят — список читается, а не зашит."""
        repo.pyproject(ROOTS)
        repo.mkdir("scripts")
        repo.write("scripts/чужой.py", "отчёт = 1\n")

        result = repo.run("check_identifier_charset")

        assert result.code == 0
