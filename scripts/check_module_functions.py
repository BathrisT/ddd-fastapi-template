"""Guard against functions defined at module level inside `app/`.

Логика принадлежит классу. Функция уровня модуля не принадлежит никому: на
месте вызова видно только имя, и рядом с ней молча заводятся дубли — так и
жили два побайтово одинаковых `_extract_name` в разных сервисах.

Само правило дублей не ловит, оно убирает место, где те заводятся молча.

Исключение одно: имя функции требует фреймворк (FastAPI-хендлер, фабрика
`Depends`, `@broker.task`). Список — `[tool.code_layout].module_functions_allowed`;
инлайновой пометки-побега нет намеренно.

Разбор через `ast`: `def` встречается в докстрингах, а вложенную функцию от
модульной грепом не отличить. Битый синтаксис пропускаем — это забота ruff.
"""

import ast
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, source_root  # noqa: E402

APP_DIR = source_root()
PYPROJECT = ROOT / "pyproject.toml"


def _allowed_prefixes() -> list[str]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    allowed = config.get("tool", {}).get("code_layout", {}).get("module_functions_allowed", [])
    return [str(entry).strip("/") for entry in allowed]


def _is_allowed(path: Path, prefixes: list[str]) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return any(relative == prefix or relative.startswith(f"{prefix}/") for prefix in prefixes)


def _module_level_functions(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []

    # Обходим и условные блоки уровня модуля: `def` под `if TYPE_CHECKING:`
    # или `try/except ImportError:` — та же функция, просто под условием.
    found: list[tuple[int, str]] = []
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.If | ast.Try | ast.With | ast.AsyncWith):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []))
            stack.extend(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
    return sorted(found)


def check_module_functions() -> list[str]:
    prefixes = _allowed_prefixes()
    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if _is_allowed(path, prefixes):
            continue
        relative = path.relative_to(ROOT).as_posix()
        for lineno, name in _module_level_functions(path):
            errors.append(f"{relative}:{lineno}: функция уровня модуля `{name}`")
    return errors


def main() -> int:
    errors = check_module_functions()
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} функций уровня модуля в app/.")
        print("Логика принадлежит классу: сущности, сервису или утилите со @staticmethod.")
        print("Контракт фреймворка — в [tool.code_layout].module_functions_allowed.")
        return 1
    print("Module functions: логика в app/ принадлежит классам. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
