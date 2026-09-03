"""Правила сценариев: один файл — один сценарий, и сценарии не композируются.

Нарушение любого из пяти означает одно: в запросе выполняется больше одного
сценария.

В `use_cases/`: ровно один класс с суффиксом `UseCase` (иначе в папке заводятся
хелперы, которым место в `services/`); единственный публичный метод `execute`
(второй публичный метод — это второй сценарий, склеенный ради общих
зависимостей); носители данных рядом не в счёт.

Во всём `app/`: держишь чужой сценарий — не коммитишь сам (сессия одна на вход,
и чужой `commit()` фиксирует недоделанную работу, а следом уходит событие,
которое уже не отозвать); вход просит не больше одного `FromDishka[*UseCase]`.

Запрещён именно коммит, а не внедрение сценария: на боевом проекте запрет
внедрения дал бы 15 срабатываний, и все ложные — диспетчер и перечислитель
своих записей не делают.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ast_shapes import is_data_carrier  # noqa: E402
from _project import ROOT, require_dir, source_root, tool_config  # noqa: E402


def _is_data_carrier(node: ast.ClassDef) -> bool:
    # Строгий режим: в каталоге сценариев `@dataclass` на классе с методами —
    # не носитель данных, а сценарий, спрятанный от проверки
    return is_data_carrier(node, strict=True)


_LAYOUT = tool_config("code_layout")
# Каталог сценариев, суффикс их имени и имя единственного метода — из конфига:
# в соседнем проекте это `interactors/`, `Interactor` и `handle`, и зашивать
# сюда местные слова значило бы раздать шаблон с проверкой, которая молчит.
USE_CASES = require_dir(
    source_root() / str(_LAYOUT.get("use_cases_dir", "application/use_cases")),
    "[tool.code_layout].use_cases_dir",
)
_SUFFIX = str(_LAYOUT.get("use_case_suffix", "UseCase"))
_ENTRY = str(_LAYOUT.get("use_case_entrypoint", "execute"))
_COMMIT = str(_LAYOUT.get("commit_method", "commit"))


def _public_methods(node: ast.ClassDef) -> list[str]:
    return [
        item.name
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        and not item.name.startswith("_")
    ]


def check_use_cases() -> list[str]:
    errors: list[str] = []
    for path in sorted(USE_CASES.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        relative = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue

        classes = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and not _is_data_carrier(node)
        ]
        scenarios = [node for node in classes if node.name.endswith(_SUFFIX)]
        strays = [node for node in classes if not node.name.endswith(_SUFFIX)]

        for node in strays:
            errors.append(
                f"{relative}:{node.lineno}: `{node.name}` — не сценарий. "
                f"Здесь живут только классы `*{_SUFFIX}`; хелперу место в services/"
            )
        if len(scenarios) > 1:
            names = ", ".join(node.name for node in scenarios)
            errors.append(f"{relative}: {len(scenarios)} сценария в одном файле ({names})")
        for node in scenarios:
            public = _public_methods(node)
            extra = [name for name in public if name != _ENTRY]
            if extra:
                errors.append(
                    f"{relative}:{node.lineno}: у `{node.name}` публичные методы "
                    f"помимо {_ENTRY}: {', '.join(sorted(extra))}. "
                    "Каждый — отдельный сценарий; общее вынеси в services/"
                )
            elif not public:
                errors.append(f"{relative}:{node.lineno}: у `{node.name}` нет `{_ENTRY}`")
    return errors


def _parsed(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover — синтаксис ловит линтер
        return None


def _annotation_text(annotation: ast.expr | None) -> str:
    """Текст аннотации без кавычек: при отложенных аннотациях тип записан
    строковой константой, и сравнение «как есть» молчало бы ровно в тех
    файлах, где они отложены, — выборочно и незаметно.
    """
    if annotation is None:
        return ""
    text = ast.unparse(annotation).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _injected_type(annotation: ast.expr | None) -> str:
    """Тип внутри `FromDishka[...]`, иначе пусто.

    Признак входа — сам `FromDishka`, а не каталог: так проверка не промахнётся
    мимо входа, заведённого в новой папке.
    """
    if not isinstance(annotation, ast.Subscript):
        return ""
    marker = annotation.value
    name = getattr(marker, "id", None) or getattr(marker, "attr", None)
    if name != "FromDishka":
        return ""
    return _annotation_text(annotation.slice)


def _args_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    return [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]


def _init_of(node: ast.ClassDef) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == "__init__":
            return item
    return None


def _calls_commit(node: ast.ClassDef) -> int:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr == _COMMIT:
                return sub.lineno
    return 0


def check_no_commit_around_scenario() -> list[str]:
    """Требование 4: держишь чужой сценарий — не коммитишь сам."""
    errors: list[str] = []
    for path in sorted(source_root().rglob("*.py")):
        tree = _parsed(path)
        if tree is None:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            init = _init_of(node)
            if init is None:
                continue
            scenarios = [
                requested
                for arg in _args_of(init)
                if arg.arg != "self" and (requested := _annotation_text(arg.annotation)).endswith(
                    _SUFFIX
                )
            ]
            if not scenarios:
                continue
            commit_line = _calls_commit(node)
            if not commit_line:
                continue
            errors.append(
                f"{relative}:{commit_line}: `{node.name}` получил сценарий "
                f"({', '.join(scenarios)}) и коммитит сам. Сессия одна на вход: этот "
                f"`{_COMMIT}()` зафиксирует чужую недоделанную работу, а следом уйдёт "
                f"событие — отозвать его нечем. Коммитит тот, чья работа"
            )
    return errors


def check_one_scenario_per_entry() -> list[str]:
    """Требование 5: вход просит не больше одного сценария."""
    errors: list[str] = []
    for path in sorted(source_root().rglob("*.py")):
        tree = _parsed(path)
        if tree is None:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            requested = [
                injected
                for arg in _args_of(node)
                if (injected := _injected_type(arg.annotation)).endswith(_SUFFIX) and injected
            ]
            if len(requested) > 1:
                errors.append(
                    f"{relative}:{node.lineno}: вход `{node.name}` просит "
                    f"{len(requested)} сценария ({', '.join(requested)}) — это два "
                    f"`commit()` на один вход и событие, ушедшее до конца работы. "
                    f"Один вход — один сценарий"
                )
    return errors


def main() -> int:
    errors = check_use_cases() + check_no_commit_around_scenario() + check_one_scenario_per_entry()
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} нарушений правил сценариев.")
        print("Один файл — один сценарий; чужой сценарий не коммитят; один commit() на вход.")
        return 1
    print("Use cases: один файл — один сценарий, один commit() на вход. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
