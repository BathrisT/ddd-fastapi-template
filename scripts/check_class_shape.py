"""Guard the shape of classes in `app/`.

Две проверки, обе про одно: класс должен честно показывать, что он такое.

**Один поведенческий класс на файл.** Носители данных (`@dataclass`,
pydantic-модели, енумы, `TypedDict`, `Protocol`, исключения, ORM-модели) в счёт
не идут и могут лежать рядом сколько угодно — `schemas/portal.py` из тридцати
моделей это нормально. А вот пять use case'ов в одном файле означают, что файл
называется не тем, что в нём лежит.

**Порт или секрет не может быть аргументом `@staticmethod`.** У статического
метода нет `self`, держать зависимость негде — и её начинают передавать
аргументом на каждый вызов. Верный признак: место вызова само прибивает первые
аргументы через `partial`, то есть руками изображает конструктор. Порт и секрет
идут в `__init__`, объект собирается в deps.

Обычные методы под вторую проверку не попадают: там порт аргументом бывает
законен (скоупнутый на запрос репозиторий).
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ast_shapes import decorator_names as _decorator_names  # noqa: E402
from _ast_shapes import is_data_carrier as _is_data_carrier  # noqa: E402
from _project import ROOT, source_root  # noqa: E402

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
    ports = _port_names()
    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if PORTS_DIR in path.parents:
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
                for arg in args:
                    if arg.arg in _SECRET_PARAMS:
                        errors.append(
                            f"{relative}:{fn.lineno}: {cls.name}.{fn.name} принимает секрет "
                            f"`{arg.arg}` аргументом — ему место в __init__"
                        )
                        continue
                    hit = _annotation_names(arg.annotation) & ports
                    if hit:
                        errors.append(
                            f"{relative}:{fn.lineno}: {cls.name}.{fn.name} принимает порт "
                            f"{', '.join(sorted(hit))} аргументом — ему место в __init__"
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
