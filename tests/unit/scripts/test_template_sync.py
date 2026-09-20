"""Посекционная сверка конфига с шаблоном.

Сравнение по файлу целиком про `pyproject.toml` не говорит ничего: имя проекта
и зависимости расходятся всегда, поэтому он лежит в `manual` — и дрейф внутри
него невидим. В соседнем боевом проекте так усохла настройка линтера и пропала
секция mypy: файл «расходится», и это правда, только не про то.

Сверяется наличие, а не значения. Поднятый порог — законный тюнинг; пропавшая
секция — потеря, обычно при разрешении конфликта «взять свою версию целиком».
"""

import sys

from tests.unit.scripts.conftest import REAL_SCRIPTS

sys.path.insert(0, str(REAL_SCRIPTS))
from template_sync import Sections

TEMPLATE = """
[tool.ruff.lint]
select = ["E"]
extend-select = ["ANN"]

[tool.mypy]
strict = false

[[tool.mypy.overrides]]
module = ["taskiq.*"]
ignore_missing_imports = true

[tool.comment_budget]
max_run = 4

[tool.poetry]
name = "template"
"""


class TemplateStub:
    """Дублёр настроек: `Sections` читает у шаблона только два списка."""

    def __init__(self, diverged: list) -> None:
        self.sections_in: list = []
        self.sections_diverged = diverged


def drift(ours: str, diverged: list | None = None) -> list:
    return Sections(TemplateStub(diverged or [])).drift(ours, TEMPLATE)


class TestSections:
    def test_nothing_lost_is_silent(self) -> None:
        assert drift(TEMPLATE) == []

    def test_missing_key_is_reported(self) -> None:
        ours = TEMPLATE.replace('extend-select = ["ANN"]\n', "")

        rows = drift(ours)

        assert len(rows) == 1
        assert "extend-select" in rows[0]

    def test_missing_section_is_reported(self) -> None:
        """Тот самый случай: секция настройки исчезла вовсе."""
        ours = TEMPLATE.replace("[tool.comment_budget]\nmax_run = 4\n", "")

        rows = drift(ours)

        assert any("tool.comment_budget]" in row and "секции нет вовсе" in row for row in rows)

    def test_emptied_section_is_reported_by_its_keys(self) -> None:
        """Секция осталась подзаголовком для вложенной, а своих ключей лишилась."""
        ours = TEMPLATE.replace("[tool.mypy]\nstrict = false\n", "")

        rows = drift(ours)

        assert any("tool.mypy]" in row and "strict" in row for row in rows)

    def test_other_values_are_not_our_business(self) -> None:
        """Поднятый порог — законный тюнинг, а не потеря."""
        ours = TEMPLATE.replace('select = ["E"]', 'select = ["E", "F", "B"]')

        assert drift(ours) == []

    def test_our_own_sections_stay_silent(self) -> None:
        ours = TEMPLATE + "\n[tool.our_own]\nkey = 1\n"

        assert drift(ours) == []

    def test_diverged_section_is_skipped(self) -> None:
        ours = TEMPLATE.replace('[tool.poetry]\nname = "template"\n', "")

        assert drift(ours, ["tool.poetry"]) == []

    def test_diverged_covers_subsections(self) -> None:
        ours = TEMPLATE.replace("ignore_missing_imports = true\n", "")

        assert drift(ours, ["tool.mypy"]) == []

    def test_array_of_tables_is_one_section(self) -> None:
        """`[[tool.mypy.overrides]]` — секция, а не список безымянных таблиц."""
        ours = TEMPLATE.replace('module = ["taskiq.*"]\n', "")

        rows = drift(ours)

        assert any("tool.mypy.overrides]" in row and "module" in row for row in rows)

    def test_broken_toml_says_so_instead_of_staying_green(self) -> None:
        rows = drift("[tool.ruff.lint\nselect = ")

        assert len(rows) == 1
        assert "не разобрать" in rows[0]
