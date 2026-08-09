"""Дописать строку в журнал линзы.

    python .claude/hooks/lens_note.py --lens 2 --round 3 \
        --ref app/interface/worker/schedule.py:47 --note "..."

Только ДОПИСЫВАЕТ. Перезаписи нет намеренно, по двум причинам сразу. Первая:
переписывать растущий список каждый раунд — это платить выходом за текст,
который уже написан, а выход дорогой. Вторая, важнее: при перезаписи агент
норовит «прибраться» и потерять половину записей, а операция «дописать» терять
не умеет в принципе.

Скрипт, а не `>>` из шелла, ради двух вещей: он проставляет номер раунда сам и
держит формат. Формат тут не косметика — по нему в следующем раунде
сопоставляют, что уже рассматривали; свободная форма разъедется к третьему
раунду, и журнал перестанет работать.

Повтор по тому же адресу молча игнорируется: смысл журнала в том, чтобы не
возвращаться к уже осмотренному месту, а не в том, чтобы собрать все
формулировки об одной строке.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review import Review  # noqa: E402

# Разбирает записанную строку на её поля. Сравнивать надо ИМЕННО поле адреса, а
# не искать подстроку по всей строке: текст заметки сплошь и рядом ссылается на
# соседние места («то же, что в app/foo.py:12»), и поиск по всей строке счёл бы
# `app/foo.py:12` уже рассмотренным. Настоящая запись по этому адресу тогда
# молча не появилась бы, а следующий раунд принял бы место за осмотренное.
_ENTRY_RE = re.compile(r"^- \[р\d+\]\s+(?P<ref>\S+)\s+—")


class Note:
    @staticmethod
    def append(claude_dir: Path, lens: int, round_no: int, ref: str, note: str) -> str:
        path = Review.notes(claude_dir, lens)
        if not path.exists():
            print(
                f"ОШИБКА: журнала {path.name} нет. Он заводится сборкой пакетов "
                "(`make review-pack`) — без неё раунд не начинают.",
                file=sys.stderr,
            )
            raise SystemExit(2)

        existing = path.read_text(encoding="utf-8")
        for line in existing.splitlines():
            match = _ENTRY_RE.match(line)
            if match and match.group("ref") == ref:
                return f"уже записано ранее, пропуск: {ref}"

        entry = f"- [р{round_no}] {ref} — {' '.join(note.split())}\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(entry)
        return f"записано: {ref}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Дописать запись в журнал линзы")
    parser.add_argument("--lens", type=int, required=True)
    parser.add_argument("--round", type=int, required=True, dest="round_no")
    parser.add_argument("--ref", required=True, help="файл:строка")
    parser.add_argument("--note", required=True, help="что рассмотрели и почему отложили")
    parser.add_argument("--cwd", default=".")
    args = parser.parse_args()

    print(
        Note.append(
            Path(args.cwd) / ".claude", args.lens, args.round_no, args.ref, args.note
        )
    )
