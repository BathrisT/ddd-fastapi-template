"""Отпечаток дерева: на нём держится и кэш, и решение «каких тестов не гонять».

Ошибка здесь не даёт красного теста — она даёт тихо пропущенную проверку. Один
такой промах уже случился: удалённый, но ещё не проиндексированный файл
считался ИЗМЕНЁННЫМ, потому что `git ls-files --cached` продолжает его называть,
и триггер «файл исчез» не срабатывал вовсе. Поймано опытом, а не проверкой —
отсюда этот файл.

Импортом, а не запуском: сравнение отметок — чистые функции. Соседние сторожа
проверяются запуском ради кода возврата, здесь возвращаемого значения хватает.
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from precommit_cache import ABSENT, changed_between, changed_status, fingerprint, latest_entry


def mark(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


class TestChangedStatus:
    def test_modified(self) -> None:
        before = {"app/x.py": mark("один")}
        after = {"app/x.py": mark("два")}

        assert changed_status(before, after) == [("M", "app/x.py")]

    def test_added(self) -> None:
        assert changed_status({}, {"app/x.py": mark("один")}) == [("A", "app/x.py")]

    def test_removed_from_the_list_entirely(self) -> None:
        assert changed_status({"app/x.py": mark("один")}, {}) == [("D", "app/x.py")]

    def test_deleted_but_still_in_the_index(self) -> None:
        """Тот самый случай: файл удалён, `git add` не делали.

        `git ls-files --cached` его называет, читать нечего — отметка `ABSENT`.
        Ключ на месте, и проверка «пути нет в словаре» считала бы файл
        изменённым, а не исчезнувшим: триггер полного прогона молчал бы.
        """
        before = {"app/x.py": mark("один")}
        after = {"app/x.py": ABSENT}

        assert changed_status(before, after) == [("D", "app/x.py")]

    def test_restored_after_being_absent(self) -> None:
        before = {"app/x.py": ABSENT}
        after = {"app/x.py": mark("один")}

        assert changed_status(before, after) == [("A", "app/x.py")]

    def test_untouched_files_are_silent(self) -> None:
        same = {"app/x.py": mark("один"), "app/y.py": mark("два")}

        assert changed_status(same, dict(same)) == []

    def test_order_is_stable(self) -> None:
        before = {"b.py": mark("б"), "a.py": mark("а")}
        after = {"b.py": mark("Б"), "a.py": mark("А")}

        assert [path for _, path in changed_status(before, after)] == ["a.py", "b.py"]


class TestChangedBetween:
    def test_lists_both_sides(self) -> None:
        before = {"a.py": mark("а"), "gone.py": mark("г")}
        after = {"a.py": mark("а"), "new.py": mark("н")}

        assert changed_between(before, after) == ["gone.py", "new.py"]


class TestFingerprint:
    def test_same_marks_give_the_same_key(self) -> None:
        marks = {"app/x.py": mark("один")}

        assert fingerprint("precommit", marks) == fingerprint("precommit", dict(marks))

    def test_content_change_moves_the_key(self) -> None:
        assert fingerprint("precommit", {"app/x.py": mark("один")}) != fingerprint(
            "precommit", {"app/x.py": mark("два")}
        )

    def test_rename_moves_the_key(self) -> None:
        """Одно и то же содержимое под другим именем — другое дерево."""
        assert fingerprint("precommit", {"app/x.py": mark("один")}) != fingerprint(
            "precommit", {"app/y.py": mark("один")}
        )

    def test_scope_separates_keys(self) -> None:
        marks = {"app/x.py": mark("один")}

        assert fingerprint("precommit", marks) != fingerprint("check", marks)


class TestLatestEntry:
    def test_picks_the_newest_of_its_scope(self) -> None:
        entries = [
            {"scope": "precommit", "saved_at": 10, "marks": {"a": "00"}},
            {"scope": "precommit", "saved_at": 30, "marks": {"b": "11"}},
            {"scope": "check", "saved_at": 50, "marks": {"c": "22"}},
        ]

        assert latest_entry(entries, "precommit")["saved_at"] == 30

    def test_entry_without_marks_is_not_a_base(self) -> None:
        """Запись старого формата база отсчёта не даёт: сравнивать не с чем."""
        entries = [{"scope": "precommit", "saved_at": 10}]

        assert latest_entry(entries, "precommit") is None

    def test_no_entries_at_all(self) -> None:
        assert latest_entry([], "precommit") is None
