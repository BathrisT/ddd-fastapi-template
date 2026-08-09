"""Guard against functions defined at module level inside `app/`.

Логика принадлежит классу — сущности, сервису или утилите со `@staticmethod`.
Функция, лежащая на уровне модуля, не принадлежит никому: на месте вызова
видно только имя, найти «что там ещё рядом родного» нельзя, и рядом с классом
такую функцию можно молча уронить. Так в проекте и завелись два дубля —
`_extract_name` в `b24_sync_service` и `adaptation_start_service` (побайтово
одинаковые) и пара `_by_role` / `_find_participant`, различавшаяся одним
условием. Первый схлопнут в `B24User.full_name`, второй — в выборки на самой
`CandidateParticipant` (`by_role`, `all_by_role`, `by_b24_user`), вместе с
девятью инлайновыми копиями того же поиска.

Само правило дублей не ловит — оно убирает место, где они заводятся молча.
Найденный дубль всё равно надо схлопывать руками, и с инлайновыми копиями
именно так и вышло: именованные функции правило вытеснило, а девять
`next((p for p in participants ...))` пришлось искать глазами.

Исключение ровно одно и по одной причине: **имя функции требует фреймворк**
(FastAPI-хендлер, фабрика `Depends`, `@broker.task`). Список — в
`[tool.code_layout].module_functions_allowed` в pyproject.toml; инлайновой
пометки-побега нет намеренно, иначе она станет способом не думать.

Разбор через `ast`, а не грепом: `def` встречается в докстрингах и строках,
а вложенную функцию от модульной грепом не отличить. Файл с битым синтаксисом
пропускаем молча — про синтаксис ругается ruff, это не наша забота.
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

    # Обходим и тела условных блоков уровня модуля: `def` внутри
    # `if TYPE_CHECKING:`, `if sys.version_info >= ...:` или
    # `try/except ImportError:` — такая же функция уровня модуля, просто
    # объявленная под условием. Смотреть только `tree.body` значило бы
    # оставить открытыми ворота, о которых знает каждый.
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
