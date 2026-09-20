"""Guard the shape of classes in `app/`.

Две проверки, обе про одно: класс должен честно показывать, что он такое.

**Один поведенческий класс на файл.** Носители данных (`@dataclass`,
pydantic-модели, енумы, `TypedDict`, `Protocol`, исключения, ORM-модели) в счёт
не идут и могут лежать рядом сколько угодно — `schemas/portal.py` из тридцати
моделей это нормально. А вот пять use case'ов в одном файле означают, что файл
называется не тем, что в нём лежит.

**Внедряемая зависимость не может быть аргументом `@staticmethod`.** Держать её
негде — нет `self`, — поэтому её подают на каждый вызов, а место вызова руками
изображает конструктор через `partial`.

Внедряемое — это порты И всё, что умеет собрать контейнер (`_providers.py`):
папка `ports/` пропускает конкретный класс-сервис, а композиция называет
внедряемое явно. Секреты ловятся отдельно, по имени параметра.

Не в счёт: обычные методы (там порт аргументом законен), сама композиция и
метод, отдающий тот же тип, что принял, — он преобразователь, а не потребитель.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ast_shapes import decorator_names as _decorator_names  # noqa: E402
from _ast_shapes import is_data_carrier as _is_data_carrier  # noqa: E402
from _project import ROOT, source_root, tool_config  # noqa: E402
from _providers import Built  # noqa: E402

APP_DIR = source_root()
PORTS_DIR = APP_DIR / "application" / "ports"

_SECRET_PARAMS = {"secret", "api_key", "password", "private_key", "token_cipher", "cipher"}


def _annotation_names(node: ast.expr | None) -> set[str]:
    """Простые имена внутри аннотации: `X | None`, `list[X]`, `dict[str, X]` → {X}."""
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Subscript):
        return _annotation_names(node.value) | _annotation_names(node.slice)
    if isinstance(node, ast.BinOp):
        return _annotation_names(node.left) | _annotation_names(node.right)
    if isinstance(node, ast.Tuple):
        return {n for e in node.elts for n in _annotation_names(e)}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _annotation_names(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return set()
    return set()


def _composition_roots() -> list[Path]:
    """Где собирают граф. Там принимать зависимости аргументом — и есть работа."""
    roots = [ROOT / path for path in tool_config("composition").get("composition_roots", [])]
    return [root for root in roots if root.is_dir()]


def _injectable() -> dict[str, str]:
    """`{тип: почему его нельзя подавать аргументом}`.

    Порты плюс всё, что умеет собрать контейнер. Папка `ports/` пропускает
    конкретный класс-сервис, а композиция называет внедряемое явно.
    """
    reasons = {name: "порт" for name in _port_names()}
    for name, where in Built.types(_composition_roots()).items():
        reasons.setdefault(name, f"его собирает контейнер, {where}")
    return reasons


def _port_names() -> set[str]:
    names: set[str] = set()
    for path in PORTS_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        names |= {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    return names


def check_one_class_per_file() -> list[str]:
    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        behavioral = [
            n for n in tree.body if isinstance(n, ast.ClassDef) and not _is_data_carrier(n)
        ]
        if len(behavioral) > 1:
            relative = path.relative_to(ROOT).as_posix()
            errors.append(
                f"{relative}: {len(behavioral)} классов с поведением "
                f"({', '.join(n.name for n in behavioral)}) — файл называется не тем, "
                "что в нём лежит"
            )
    return errors


def check_static_dependencies() -> list[str]:
    injectable = _injectable()
    composition = _composition_roots()
    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if PORTS_DIR in path.parents or any(root in path.parents for root in composition):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for fn in cls.body:
                if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if not _decorator_names(fn) & {"staticmethod", "classmethod"}:
                    continue
                args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
                # Метод, отдающий тот же тип, что принял, — преобразователь, а не
                # потребитель: `AutonomousEngine.for_(engine) -> AsyncEngine`
                # делает из движка движок, зависимость при этом живёт в чужом
                # `__init__`. Потребитель возвращает что-то другое или ничего.
                produced = _annotation_names(fn.returns)
                for arg in args:
                    if arg.arg in _SECRET_PARAMS:
                        errors.append(
                            f"{relative}:{fn.lineno}: {cls.name}.{fn.name} принимает секрет "
                            f"`{arg.arg}` аргументом — ему место в __init__"
                        )
                        continue
                    hit = sorted(_annotation_names(arg.annotation) & set(injectable))
                    if hit and not set(hit) <= produced:
                        named = ", ".join(f"`{name}` ({injectable[name]})" for name in hit)
                        errors.append(
                            f"{relative}:{fn.lineno}: {cls.name}.{fn.name} принимает "
                            f"зависимость аргументом: {named}. Держать её у статического "
                            "метода негде, поэтому её подают на каждый вызов — место "
                            "зависимости в `__init__`"
                        )
    return errors


def main() -> int:
    errors = check_one_class_per_file() + check_static_dependencies()
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} нарушений формы класса.")
        return 1
    print("Class shape: один класс с поведением на файл, зависимости в конструкторе. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
