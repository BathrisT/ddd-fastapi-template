"""Guard against one business rule living as a constant in many files.

Правило бизнеса, скопированное в N модулей, меняется в N местах — и меняется
не везде. Так «рассылки идут с 9 утра» разъехалось по семи задачам шедулера
под четырьмя разными именами (`_SEND_HOUR`, `_REPORT_SEND_HOUR`,
`_SURVEY_SEND_HOUR`, `_CLOSE_HOUR`), и поменять час рассылки означало найти
все семь.

Отличить «правило бизнеса» от «технической константы» синтаксически нельзя:
`_MAX_QUESTIONS = 12` и `_MAX_FILES = 10_000` неразличимы по форме. Поэтому
проверка ловит не смысл, а симптом — повторение. Одно и то же значение,
присвоенное модульным константам в нескольких файлах, почти всегда означает
скопированное правило.

Порог в `[tool.code_layout].duplicated_constant_files`.
"""

import ast
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, source_root  # noqa: E402

APP_DIR = source_root()
PYPROJECT = ROOT / "pyproject.toml"
_DEFAULT_THRESHOLD = 5
# Значения, повторение которых ничего не значит: флаги и вырожденные числа
_TRIVIAL = {0, 1, -1, "", None}


def _threshold() -> int:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8")).get("tool", {})
    layout = config.get("code_layout", {})
    return int(layout.get("duplicated_constant_files", _DEFAULT_THRESHOLD))


def _literal(node: ast.expr) -> object | None:
    """Значение константы, если оно литерал. Иначе None — такие не сравниваем."""
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, int | float | str) else None


def _module_constants(path: Path) -> dict[str, object]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return {}
    found: dict[str, object] = {}
    # Тело модуля И тела классов. Правило раскладки выдавливает весь код в
    # классы, то есть ровно туда, где размноженную константу видно не было:
    # `class Policy: SEND_HOUR = 9` в пяти файлах — то же скопированное
    # правило, что и пять модульных констант.
    scopes: list[ast.stmt] = list(tree.body)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            scopes.extend(node.body)
    for node in scopes:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        if not targets or node.value is None:
            continue
        name = targets[0]
        if not name.lstrip("_").isupper():
            continue
        value = _literal(node.value)
        if value is None or value in _TRIVIAL:
            continue
        found[name] = value
    return found


def _tokens(name: str) -> set[str]:
    """Слова имени: `_REPORT_SEND_HOUR` → {REPORT, SEND, HOUR}."""
    return {part for part in name.upper().split("_") if part}


def check_duplicated_constants() -> list[str]:
    threshold = _threshold()
    by_value: dict[object, list[tuple[str, str]]] = defaultdict(list)
    for path in sorted(APP_DIR.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        for name, value in _module_constants(path).items():
            by_value[value].append((relative, name))

    errors: list[str] = []
    for value, places in sorted(by_value.items(), key=lambda kv: -len(kv[1])):
        if len({f for f, _ in places}) < threshold:
            continue
        # Одного совпадения значений мало: `5` — это и суббота, и НДС, и лимит
        # неудачных входов, и сложность блока. Такая находка не подсказывает
        # ничего, а приучает пропускать проверку не глядя.
        #
        # Признак СКОПИРОВАННОГО правила — общее слово в именах: ровно так
        # разъехался час рассылки (`_SEND_HOUR`, `_REPORT_SEND_HOUR`,
        # `_SURVEY_SEND_HOUR`, `_CLOSE_HOUR` — все про HOUR). Поэтому внутри
        # одного значения группируем ещё и по общему слову.
        by_token: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for relative, name in places:
            for token in _tokens(name):
                by_token[token].append((relative, name))

        for token, group in sorted(by_token.items()):
            files = {f for f, _ in group}
            if len(files) < threshold:
                continue
            names = sorted({n for _, n in group})
            errors.append(
                f"значение {value!r} задано константой в {len(files)} файлах "
                f"под именами про «{token}»: {', '.join(names)}\n"
                + "".join(f"    {f}: {n}\n" for f, n in sorted(group))
                + "  Похоже на одно правило, размноженное копипастой. "
                "Место общего правила — domain/catalog/."
            )
    return errors


def main() -> int:
    errors = check_duplicated_constants()
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} значений размножено по константам.")
        return 1
    print("Duplicated constants: размноженных правил не видно. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
