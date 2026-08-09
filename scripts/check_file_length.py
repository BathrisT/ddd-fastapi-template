"""Guard against a file in `app/` growing into a swamp.

Правило «один файл — одна логическая группа» никак не проверялось, и один
файл дорос до 1244 строк (`bot_text.py`), собрав в себе меню плана, карточки
блоков, тесты, опросники, цели и черновики целей. Найти в нём «что рядом
родного» невозможно, а форма кода тут ни при чём: класс с 88 методами был бы
ровно такой же помойкой. Объём ловится только лимитом.

Порог из `[tool.code_layout].max_lines` в pyproject.toml. Медиана файла в
проекте — 33 строки, так что 300 это девять медиан.

В ruff такого правила нет: pylint'овый `C0302 too-many-lines` в него не
портирован (проверено на 0.8.6), а тащить pylint ради одного правила дороже,
чем этот скрипт.

Проверка распространяется на весь `app/`, включая роутеры: контракт фреймворка
оправдывает форму функции, но не объём файла.
"""

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, source_root  # noqa: E402

APP_DIR = source_root()
PYPROJECT = ROOT / "pyproject.toml"
_DEFAULT_MAX_LINES = 300


def _max_lines() -> int:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return int(config.get("tool", {}).get("code_layout", {}).get("max_lines", _DEFAULT_MAX_LINES))


def check_file_length() -> list[str]:
    limit = _max_lines()
    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > limit:
            relative = path.relative_to(ROOT).as_posix()
            errors.append(f"{relative}: {count} строк при лимите {limit}")
    return errors


def main() -> int:
    errors = check_file_length()
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} файлов длиннее лимита.")
        print("Один файл — одна логическая группа. Режь по темам, а не по объёму.")
        return 1
    print("File length: файлы в app/ в пределах лимита. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
