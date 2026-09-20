"""Пометки, глушащие проверки, требуют объяснения рядом с собой.

У сторожа против молчаливого обхода правил есть своя история молчаливого
обхода: обе проверки жили конвейерами `rg` в Makefile, ripgrep не
предустановлен ни на одной ОС, и без него обе строки завершались нулём.
Поэтому тест на «пометка без объяснения ловится» здесь не единственный: нужен
и тест на то, что негодная настройка даёт отказ, а не пустой успех.
"""

from tests.unit.scripts.conftest import Repo


class TestEscapeHatches:
    def test_file_wide_ruff_noqa_without_a_reason(self, repo: Repo) -> None:
        repo.write(
            "app/infrastructure/db/models/user.py",
            """
            # ruff: noqa
            class UserORM: ...
            """,
        )

        result = repo.run("check_escape_hatches")

        assert result.code == 1
        assert result.mentions("ruff отключён на весь файл")

    def test_file_wide_noqa_with_a_reason_anywhere_in_the_file(self, repo: Repo) -> None:
        """Пометка выключает файл целиком — значит и оправдание относится к нему."""
        repo.write(
            "app/infrastructure/db/models/user.py",
            """
            # ruff: noqa
            # allow-ruff-noqa: циклический импорт SQLAlchemy
            class UserORM: ...
            """,
        )

        result = repo.run("check_escape_hatches")

        assert result.code == 0

    def test_mock_in_app_without_a_reason(self, repo: Repo) -> None:
        repo.write(
            "app/services/sender.py",
            """
            from unittest.mock import MagicMock


            class Sender:
                def build(self) -> MagicMock:
                    return MagicMock()
            """,
        )

        result = repo.run("check_escape_hatches")

        assert result.code == 1
        assert result.mentions("мок вместо дублёра")

    def test_mock_reason_is_per_line(self, repo: Repo) -> None:
        """Область `line`: объяснение обязано стоять в той же строке.

        Иначе одно оправдание наверху файла прикрывало бы всё, что ниже.
        """
        repo.write(
            "app/services/sender.py",
            """
            class Sender:
                def build(self):
                    return MagicMock()  # allow-mock: дублёр чужого протокола
            """,
        )

        result = repo.run("check_escape_hatches")

        assert result.code == 0

    def test_clean_app_passes(self, repo: Repo) -> None:
        repo.write("app/services/sender.py", "class Sender: ...\n")

        result = repo.run("check_escape_hatches")

        assert result.code == 0
        assert result.mentions("объяснены все")

    def test_custom_rule_from_the_config(self, repo: Repo) -> None:
        """Побеги у каждого проекта свои — правила приходят из конфига."""
        repo.pyproject(
            """
            [[tool.escape_hatches.rules]]
            name = "тайпчекер отключён"
            patterns = ["# mypy: ignore-errors"]
            allow = "# allow-mypy-ignore:"
            scope = "file"
            why = "Файл без типов — это файл без проверки."
            """
        )
        repo.write(
            "app/services/sender.py",
            """
            # mypy: ignore-errors
            class Sender: ...
            """,
        )

        result = repo.run("check_escape_hatches")

        assert result.code == 1
        assert result.mentions("тайпчекер отключён")

    def test_unusable_config_is_an_error_not_a_pass(self, repo: Repo) -> None:
        """Правило без `allow` ничем не оправдывается — это не «нет правил», это ошибка."""
        repo.pyproject(
            """
            [[tool.escape_hatches.rules]]
            name = "сломанное правило"
            patterns = ["# ruff: noqa"]
            """
        )

        result = repo.run("check_escape_hatches")

        assert result.code == 2
        assert result.mentions("ОШИБКА НАСТРОЙКИ")


PROTECTED = """
[tool.escape_hatches.protected]
lists = ["tool.ruff.lint.ignore", "tool.mypy.disable_error_code"]

[tool.escape_hatches.protected.codes]
PLC0415 = "импорт внутри функции — штатный способ обойти tach"
DTZ = "наивное время"
"""

RUFF_IGNORES = """
[tool.ruff.lint]
ignore = ["PLR0913", "PLC0415"]
"""

MYPY_DISABLES = """
[tool.mypy]
disable_error_code = ["DTZ005"]
"""

HARMLESS = """
[tool.ruff.lint]
ignore = ["E501"]
"""


class TestProtectedCodes:
    def test_protected_code_in_the_global_list(self, repo: Repo) -> None:
        """Тот самый случай: под кодом в общем списке прячется обход tach."""
        repo.pyproject(PROTECTED + RUFF_IGNORES)

        result = repo.run("check_escape_hatches")

        assert result.code == 1
        assert result.mentions("PLC0415")
        assert result.mentions("обойти tach")

    def test_family_selector_covers_the_code(self, repo: Repo) -> None:
        """`DTZ` в защищённых гасится и записью `DTZ005`."""
        repo.pyproject(PROTECTED + MYPY_DISABLES)

        result = repo.run("check_escape_hatches")

        assert result.code == 1

    def test_neighbouring_letters_are_not_a_match(self, repo: Repo) -> None:
        """`A` — это flake8-builtins, а не начало `ANN401`."""
        repo.pyproject(
            """
            [tool.escape_hatches.protected]
            lists = ["tool.ruff.lint.ignore"]

            [tool.escape_hatches.protected.codes]
            ANN401 = "`Any` без объяснения"

            [tool.ruff.lint]
            ignore = ["A"]
            """
        )

        result = repo.run("check_escape_hatches")

        assert result.code == 0

    def test_unprotected_code_is_allowed(self, repo: Repo) -> None:
        repo.pyproject(PROTECTED + HARMLESS)

        result = repo.run("check_escape_hatches")

        assert result.code == 0

    def test_without_the_list_the_rule_is_off(self, repo: Repo) -> None:
        """Нет `[tool.escape_hatches.protected]` — нет и проверки."""
        repo.pyproject(RUFF_IGNORES)

        result = repo.run("check_escape_hatches")

        assert result.code == 0
