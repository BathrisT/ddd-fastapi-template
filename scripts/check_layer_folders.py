"""Guard the folder axis of each layer.

Подпапка слоя называет РОЛЬ — что за штука внутри (`models`, `ports`,
`use_cases`). Предметная область — что оно ПРО — живёт уровнем ниже, внутри
роли. Смешивать две оси на одном уровне нельзя: так `application/messaging`
оказался в одном ряду с `dto`/`ports`/`services`/`use_cases`, хотя по роли он
DTO — структура, пересекающая границу application → infrastructure/channels.

Для `domain` и `application` набор ролей закрыт и перечислен в
`[tool.code_layout.layer_folders]`. Для `infrastructure` и `interface` ось
другая — «к чему адаптируемся» и «по какому протоколу входим», её заранее не
перечислить, поэтому там проверяются только запрещённые имена.

Запрещённые имена (`utils`, `helpers`, `common`, ...) не называют ничего: это
папки, куда кладут, чтобы не принимать решение. Проверяются на всей глубине,
в любом слое.
"""

import ast
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ast_shapes import base_names  # noqa: E402
from _project import ROOT, source_root  # noqa: E402

APP_DIR = source_root()
PYPROJECT = ROOT / "pyproject.toml"
_IGNORED = {"__pycache__"}


def _config() -> dict:
    raw = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return raw.get("tool", {}).get("code_layout", {})


def _subdirs(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(p for p in path.iterdir() if p.is_dir() and p.name not in _IGNORED)


def check_layer_roles(config: dict) -> list[str]:
    """Подпапки слоя обязаны быть ролями из списка."""
    errors: list[str] = []
    for layer, allowed in config.get("layer_folders", {}).items():
        layer_dir = ROOT / layer
        if not layer_dir.is_dir():
            errors.append(f"{layer}: слоя нет — правило ссылается в пустоту")
            continue
        for sub in _subdirs(layer_dir):
            if sub.name not in allowed:
                errors.append(
                    f"{layer}/{sub.name}: не роль. Разрешены: {', '.join(sorted(allowed))}. "
                    "Предметной области место уровнем ниже, внутри роли."
                )
    return errors


def check_protocols_in_ports() -> list[str]:
    """`class X(Protocol)` в ядре — это контракт, а контракты живут в ports/.

    Три фабрики установки портала лежали внутри `use_cases/portal/install.py`:
    формально порты, физически посреди сценария, и найти их можно было только
    случайно.

    Проверяются только `application` и `domain`. Инфраструктура и интерфейс —
    адаптеры, и шов между двумя их собственными классами (`TokenRefresher`,
    `KnowledgeBackend`) это их дело, а не контракт ядра.
    """
    ports_dir = APP_DIR / "application" / "ports"
    scopes = (APP_DIR / "application", APP_DIR / "domain")
    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if ports_dir in path.parents or not any(s in path.parents for s in scopes):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # `base_names` разворачивает подписку: у `class X(Protocol[T])`
            # база это ast.Subscript, и прямой getattr по ней даёт None —
            # дженерик-контракт вне ports/ проходил молча
            if "Protocol" in base_names(node):
                relative = path.relative_to(ROOT).as_posix()
                errors.append(
                    f"{relative}:{node.lineno}: Protocol `{node.name}` вне ports/ — "
                    "контракту место в application/ports/"
                )
    return errors


def check_dir_size(config: dict) -> list[str]:
    """Плоский список из полусотни файлов — это не раскладка, а свалка."""
    limit = int(config.get("max_files_per_dir", 0))
    if not limit:
        return []
    errors: list[str] = []
    for path in sorted([APP_DIR, *APP_DIR.rglob("*")]):
        if not path.is_dir() or path.name in _IGNORED:
            continue
        count = len([p for p in path.glob("*.py") if p.name != "__init__.py"])
        if count > limit:
            relative = path.relative_to(ROOT).as_posix()
            errors.append(
                f"{relative}: {count} файлов при лимите {limit} — разбей на подмодули по смыслу"
            )
    return errors


def check_banned_names(config: dict) -> list[str]:
    """Имена-помойки — на любой глубине любого слоя."""
    banned = {str(name) for name in config.get("banned_folder_names", [])}
    if not banned:
        return []
    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*")):
        if path.is_dir() and path.name in banned:
            relative = path.relative_to(ROOT).as_posix()
            errors.append(f"{relative}: имя папки ничего не называет — назови по содержимому")
    return errors


def main() -> int:
    config = _config()
    errors = (
        check_layer_roles(config)
        + check_protocols_in_ports()
        + check_dir_size(config)
        + check_banned_names(config)
    )
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} нарушений раскладки по папкам.")
        return 1
    print("Layer folders: подпапки слоёв называют роли. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
