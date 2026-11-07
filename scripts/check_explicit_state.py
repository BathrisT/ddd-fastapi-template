"""Запрет специальных методов и isinstance в слоях бизнес-логики и композиции."""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, source_root, tool_config


def _is_check(node: ast.AST | None, checks: set[str], builtins: set[str]) -> bool:
    return (isinstance(node, ast.Name) and node.id in checks) or (
        isinstance(node, ast.Attribute)
        and node.attr == "isinstance"
        and isinstance(node.value, ast.Name)
        and node.value.id in builtins
    )


def check_explicit_state() -> list[str]:
    errors: list[str] = []
    root = source_root()
    config = tool_config("explicit_state")
    layers = config.get("isinstance_layers", ["application", "composition", "domain"])
    paths = sorted(root.rglob("*.py"))
    if not paths:
        return [f"{root}: нет исходников для проверки"]
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"{relative}: ошибка разбора: {exc}")
            continue
        restricted = path.relative_to(root).parts[0] in layers
        builtins = {"builtins"}
        checks = {"isinstance"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                builtins.update(a.asname or a.name for a in node.names if a.name == "builtins")
            if isinstance(node, ast.ImportFrom) and node.module == "builtins":
                checks.update(a.asname or a.name for a in node.names if a.name == "isinstance")
        for _ in range(len(list(ast.walk(tree)))):
            before = checks.copy()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign | ast.AnnAssign) and _is_check(
                    node.value, checks, builtins
                ):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    checks.update(target.id for target in targets if isinstance(target, ast.Name))
            if before == checks:
                break
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if (
                    node.name.startswith("__")
                    and node.name.endswith("__")
                    and node.name != "__init__"
                ):
                    errors.append(
                        f"{relative}:{node.lineno}: специальный метод `{node.name}` запрещён; разрешён только __init__"
                    )
            if not restricted or not isinstance(node, ast.Call):
                continue
            target = node.func
            if _is_check(target, checks, builtins):
                errors.append(f"{relative}:{node.lineno}: isinstance запрещён в этом слое")
    return errors


def main() -> int:
    errors = check_explicit_state()
    for error in errors:
        print(error)
    if not errors:
        print("Explicit state: специальные методы и isinstance проверены. OK.")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
