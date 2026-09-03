"""Guard against non-latin identifiers anywhere in the project's code.

Комментарии и докстроки на русском — норма, синтаксис — нет: имя `отчёт` не
набрать на чужой раскладке и не сгрепать вместе с `отчет`, а `Sуnc` с
кириллической `у` от `Sync` глазами не отличить вовсе.

Проверяются ИМЕНА — переменных, функций, классов, аргументов, атрибутов,
псевдонимов импорта, — и имена файлов с папками: имя модуля тоже идентификатор.
Разбор через `tokenize`: он отдаёт ровно NAME-токены, тогда как обход дерева
пришлось бы вести по трём десяткам видов узлов, и забытый дал бы дыру молча.

Смотрим не только `app/`: правило про язык, а не про слой. Корни —
`[tool.identifier_charset].roots`, каждый обязан существовать.
"""

import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural, require_dir, source_root, tool_config  # noqa: E402

_DEFAULT_ROOTS = ["app", "tests", "scripts", "migrations"]


def _roots() -> list[Path]:
    configured = tool_config("identifier_charset").get("roots", _DEFAULT_ROOTS)
    paths: list[Path] = []
    for entry in configured:
        setting = f"[tool.identifier_charset].roots = {entry!r}"
        paths.append(require_dir(ROOT / str(entry), setting))
    if not paths:
        paths.append(source_root())
    return paths


def _bad_names(path: Path) -> list[tuple[int, str]]:
    try:
        with path.open("rb") as handle:
            return [
                (token.start[0], token.string)
                for token in tokenize.tokenize(handle.readline)
                if token.type == tokenize.NAME and not token.string.isascii()
            ]
    except (SyntaxError, tokenize.TokenError, UnicodeDecodeError):
        return []


def check_identifier_charset() -> list[str]:
    errors: list[str] = []
    seen: set[Path] = set()
    for root in _roots():
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or path in seen:
                continue
            seen.add(path)
            relative = path.relative_to(ROOT).as_posix()
            for part in path.relative_to(ROOT).parts:
                if not part.isascii():
                    errors.append(f"{relative}: имя файла или папки `{part}` не на латинице")
                    break
            for lineno, name in _bad_names(path):
                errors.append(f"{relative}:{lineno}: имя `{name}` не на латинице")
    return errors


def main() -> int:
    errors = check_identifier_charset()
    if errors:
        for error in errors:
            print(error)
        print(f"\n{plural(len(errors), 'имя', 'имени', 'имён')} вне латиницы.")
        print("Комментарии и докстроки — на любом языке; синтаксис — только латиницей.")
        return 1
    print("Identifier charset: имена в коде на латинице. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
