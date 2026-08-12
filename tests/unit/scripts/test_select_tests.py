"""Отбор тестов: правило, которое решает, ЧЕГО НЕ ГОНЯТЬ.

Ошибка здесь выглядит как зелёный прогон, поэтому проверяется не «выбирает ли
он нужное», а НЕ ОТСЕИВАЕТ ЛИ ОН ЛИШНЕЕ. Каждый тест ниже воспроизводит класс
невидимой связи, из-за которого отбор обязан сдаться и прогнать всё.

Импортом, а не запуском: `decide` — чистая функция от списка изменений и
правил, конфиг читают вызывающие. Сторожа в соседних файлах проверяются
запуском ради кода возврата, здесь возвращаемого значения достаточно.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from select_tests import covered_by, decide, narrow

ZONES = [
    {"paths": ["app/**"], "tests": ["tests/unit/app", "tests/integration"]},
    {"paths": ["scripts/**"], "tests": ["tests/unit/scripts"]},
]
TRIGGERS = ("migrations/**", "**/conftest.py")


class TestSelects:
    def test_scripts_alone_do_not_drag_the_slow_tests(self) -> None:
        reason, targets = decide([("M", "scripts/check_effects.py")], ZONES, TRIGGERS)

        assert reason == ""
        assert targets == ["tests/unit/scripts"]

    def test_app_alone_does_not_drag_the_guard_tests(self) -> None:
        reason, targets = decide([("M", "app/domain/models/user.py")], ZONES, TRIGGERS)

        assert reason == ""
        assert targets == ["tests/integration", "tests/unit/app"]

    def test_two_zones_give_the_union(self) -> None:
        rows = [("M", "app/domain/models/user.py"), ("M", "scripts/check_effects.py")]

        _, targets = decide(rows, ZONES, TRIGGERS)

        assert targets == ["tests/integration", "tests/unit/app", "tests/unit/scripts"]

    def test_nothing_changed_selects_nothing(self) -> None:
        assert decide([], ZONES, TRIGGERS) == ("", [])


class TestGivesUp:
    """Каждый случай — свой класс связи, которой в карте зависимостей нет."""

    def test_unknown_file_is_not_assumed_harmless(self) -> None:
        reason, targets = decide([("M", "app_extra/thing.py")], ZONES, TRIGGERS)

        assert "вне известных зон" in reason
        assert targets == []

    def test_non_python_file(self) -> None:
        """Данные, шаблоны, конфиги: исполненных строк питона в них нет."""
        reason, _ = decide([("M", "app/templates/letter.html")], ZONES, TRIGGERS)

        assert "не питоновский" in reason

    def test_deleted_file(self) -> None:
        """У пропавшей проверки нет строк, которые кто-то мог бы исполнить."""
        reason, _ = decide([("D", "app/domain/models/user.py")], ZONES, TRIGGERS)

        assert "исчез" in reason

    def test_migration_drags_everything(self) -> None:
        """Схема приезжает фикстурой сессионного скоупа: её исполняет один тест
        из шестнадцати, и точный отбор выбрал бы ровно его одного."""
        reason, _ = decide([("A", "migrations/versions/0003_thing.py")], ZONES, TRIGGERS)

        assert "триггер" in reason

    def test_conftest_drags_everything(self) -> None:
        reason, _ = decide([("M", "tests/integration/conftest.py")], ZONES, TRIGGERS)

        assert "триггер" in reason

    def test_one_bad_file_cancels_the_whole_selection(self) -> None:
        """Отбор — это утверждение обо ВСЕЙ правке, а не о каждом файле порознь."""
        rows = [("M", "app/domain/models/user.py"), ("M", "docker-compose.yml")]

        reason, targets = decide(rows, ZONES, TRIGGERS)

        assert reason != ""
        assert targets == []


class TestNarrow:
    """`make test-unit` спрашивает то же правило, но про свою часть набора."""

    def test_the_form_the_makefile_actually_passes(self) -> None:
        """`TEST_DIR = ./tests`, поэтому спрашивают `./tests/unit` — с точкой.

        Пока сравнение было строковым, `./tests/unit` не пересекалось с
        `tests/unit/app` ни в одну сторону: отбор возвращал пусто, и
        `make test-unit` вместе с `make check` не запускали НИ ОДНОГО теста,
        сообщая «менять нечего». Прежние тесты проверяли форму без точки — ту,
        которой в Makefile нет.
        """
        assert narrow(["tests/unit/app", "tests/integration"], "./tests/unit") == ["tests/unit/app"]

    def test_trailing_slash_and_backslashes(self) -> None:
        assert narrow(["tests/unit/app"], ".\\tests\\unit\\") == ["tests/unit/app"]

    def test_keeps_only_what_lies_inside(self) -> None:
        assert narrow(["tests/unit/scripts", "tests/integration"], "tests/unit") == [
            "tests/unit/scripts"
        ]

    def test_a_wider_target_shrinks_to_the_asked_part(self) -> None:
        """Цель накрывает подмножество — гнать надо подмножество, а не цель."""
        assert narrow(["tests/unit"], "tests/unit/app") == ["tests/unit/app"]

    def test_nothing_in_common(self) -> None:
        assert narrow(["tests/integration"], "tests/unit") == []


class TestCoveredBy:
    """Сверка отсеянного с упавшим: по этому предикату полный прогон решает,
    соврало ли правило отбора."""

    def test_file_inside_a_selected_directory(self) -> None:
        assert covered_by("tests/unit/app/test_user.py", ["tests/unit/app"])

    def test_file_outside_it(self) -> None:
        assert not covered_by("tests/unit/scripts/test_check_effects.py", ["tests/unit/app"])

    def test_prefix_is_not_a_directory(self) -> None:
        """`tests/unit/apple/...` не лежит внутри `tests/unit/app`."""
        assert not covered_by("tests/unit/apple/test_x.py", ["tests/unit/app"])
