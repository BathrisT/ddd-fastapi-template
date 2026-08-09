"""Guard the database boundary: сессию открывает композиция, SQL пишут репозитории.

Два правила, оба про одно — кто имеет право разговаривать с базой напрямую.

**Сессию создаёт тот, кто отвечает за её жизнь.** У сессии есть владелец:
контейнер открывает её на вход (`RequestProvider.session`) и закрывает вместе
с ним, а короткие записи, которые обязаны пережить транзакцию сценария, идут
через единственный фасад автономной работы. Сервис, открывающий сессию сам,
эту границу стирает: он решает за сценарий, что и когда зафиксировано, а
`commit()` посреди чужой транзакции фиксирует ещё и то, что сценарий
фиксировать не собирался. Так это и было в `B24ConversationRepair` — две
сессии на один вызов, обе руками.

**SQL живёт в репозиториях.** Правило 4 CLAUDE.md («репозитории возвращают
доменные модели») описывает выход, но не вход: пока `sa.update(...)` можно
написать где угодно, запрос к таблице появляется в сервисе канала, в сценарии,
в хендлере — и рядом с ним поселяются ORM-модели, а вместе с ними знание о
колонках там, где должно быть знание о предметной области.

Оба списка — в `[tool.db_access]` pyproject.toml, потому что путей-исключений
у каждого проекта свои: где-то фасад автономной записи один, где-то их два.
Инлайновой пометки-побега нет намеренно, как и в соседних сторожах: завести
исключение можно только правкой конфига, и она видна в ревью.

Проверяется только `app/` — миграции и тесты работают с базой по определению.
"""

import ast
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, source_root  # noqa: E402

APP_DIR = source_root()
PYPROJECT = ROOT / "pyproject.toml"


def _config() -> dict:
    raw = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return raw.get("tool", {}).get("db_access", {})


def _allowed(path: Path, prefixes: list[str]) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return any(relative == p or relative.startswith(f"{p}/") for p in prefixes)


def _sources() -> list[tuple[Path, str, ast.Module]]:
    files: list[tuple[Path, str, ast.Module]] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        files.append((path, path.relative_to(ROOT).as_posix(), tree))
    return files


def _aliases(tree: ast.Module, names: set[str]) -> set[str]:
    """Имена вместе с псевдонимами импорта.

    `from sqlalchemy.ext.asyncio import AsyncSession as S` обходил бы сверку по
    имени — ровно эта дыра закрыта в соседних сторожах.
    """
    local = set(names)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            local.update(a.asname for a in node.names if a.name in names and a.asname)
    return local


def check_session_owners(config: dict) -> list[str]:
    """Сессию открывают только те, кто за неё отвечает."""
    markers = set(config.get("session_markers", []))
    owners = config.get("session_owners", [])
    if not markers:
        return []

    errors: list[str] = []
    for path, relative, tree in _sources():
        if _allowed(path, owners):
            continue
        local = _aliases(tree, markers)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in local:
                errors.append(
                    f"{relative}:{node.lineno}: `{name}(...)` — сессия создаётся вне композиции. "
                    "Сессию на вход открывает контейнер, автономную запись — её единственный "
                    "фасад; иначе объект сам решает, что и когда зафиксировано."
                )
    return errors


def check_orm_stays_in_repositories(config: dict) -> list[str]:
    """SQLAlchemy — только там, где ей положено быть."""
    packages = set(config.get("orm_packages", []))
    allowed = config.get("orm_allowed", [])
    if not packages:
        return []

    errors: list[str] = []
    for path, relative, tree in _sources():
        if _allowed(path, allowed):
            continue
        for node in ast.walk(tree):
            imported = ""
            if isinstance(node, ast.Import):
                imported = next(
                    (a.name for a in node.names if a.name.split(".")[0] in packages), ""
                )
            elif isinstance(node, ast.ImportFrom):
                imported = node.module or ""
                if imported.split(".")[0] not in packages:
                    imported = ""
            if imported:
                errors.append(
                    f"{relative}:{node.lineno}: импорт `{imported}` вне слоя доступа к данным. "
                    "Запрос к таблице пишет репозиторий и возвращает доменную модель — "
                    "иначе знание о колонках расползается по сервисам и сценариям."
                )
    return errors


def main() -> int:
    config = _config()
    errors = check_session_owners(config) + check_orm_stays_in_repositories(config)
    if errors:
        print("Доступ к базе мимо границы:\n")
        for error in errors:
            print(f"  {error}")
        print(f"\nВсего: {len(errors)}")
        return 1
    print("Доступ к базе: сессии из композиции, SQL в репозиториях")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
