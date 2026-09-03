"""Guard the injection boundary: зависимости из контейнера, не из фреймворка.

Правило целиком — `docs/rules/композиция-и-скоупы.md`. Здесь один его пункт:
остальные закрыты `tach` (он запрещает сам импорт инфраструктуры) и самим
контейнером (граф с процессным, зависящим от привходового, не соберётся).
А `Depends` через `tach` не выразить: fastapi роутам нужен и так, для
`@router.get`, — разделить «ради маршрута» и «ради внедрения» можно только по
месту вызова.

Проверка не против человека, а против ИДИОМЫ: агенту говорят «добавь эндпоинт,
ему нужен репозиторий», и он пишет `Depends(get_plan_repo)`, потому что так
написаны все примеры FastAPI на свете.

Белый список — `[tool.composition].injection_allowed`; там только верификаторы
входа: выбор способа проверки принадлежит маршрутизации, а не контейнеру.
"""

import ast
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, names_repository, require_dir, source_root  # noqa: E402

APP_DIR = source_root()
PYPROJECT = ROOT / "pyproject.toml"


def _config() -> dict:
    raw = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return raw.get("tool", {}).get("composition", {})


def _allowed(path: Path, prefixes: list[str]) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return any(relative == p or relative.startswith(f"{p}/") for p in prefixes)


def check_framework_injection(config: dict) -> list[str]:
    markers = set(config.get("injection_markers", []))
    allowed = config.get("injection_allowed", [])
    verifiers = set(config.get("verifiers", []))
    if not markers:
        return []

    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if _allowed(path, allowed):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        # Псевдоним импорта — тот же маркер под другим именем:
        # `from fastapi import Depends as Dep` обходил сверку по имени.
        local_markers = set(markers)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                local_markers.update(
                    alias.asname for alias in node.names if alias.name in markers and alias.asname
                )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in local_markers:
                continue
            # Обёртка вокруг верификатора — это объявление контракта входа, а не
            # внедрение зависимости: способ проверки принадлежит маршрутизации
            wrapped = node.args[0] if node.args else None
            if wrapped is not None and getattr(wrapped, "id", None) in verifiers:
                continue
            errors.append(
                f"{relative}:{node.lineno}: `{name}(...)` — зависимость от фреймворка. "
                "Зависимости приходят из контейнера; на границе остаётся только "
                "проверка того, кем представляется запрос."
            )
    return errors


def check_no_service_locator(config: dict) -> list[str]:
    """Контейнер не передаётся объекту, чтобы тот достал себе зависимости.

    Ни одна проверка на это не смотрела: `Depends` нет, инфраструктура не
    импортируется, `tach` доволен — а на месте вызова не видно, что объекту
    нужно. Ловим два входа: параметр и `request.state.dishka_container`.
    """
    banned_types = set(config.get("container_types", []))
    banned_attrs = set(config.get("container_attrs", []))
    if not banned_types and not banned_attrs:
        return []

    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        # Композиции контейнер положен по определению. Путь из конфига, а не
        # литералом: в соседнем проекте сборка зовётся `di/` или `bootstrap/`,
        # и зашитое имя означало бы отказ на самом корне сборки.
        if _allowed(path, config.get("composition_roots", [])):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        # Псевдоним импорта — тот же тип под другим именем:
        # `from dishka import AsyncContainer as Box` обходил сверку по имени.
        # Ровно эта дыра была закрыта для `Depends` выше, а здесь — нет.
        local_types = set(banned_types)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                local_types.update(
                    alias.asname
                    for alias in node.names
                    if alias.name in banned_types and alias.asname
                )
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in banned_attrs:
                errors.append(
                    f"{relative}:{node.lineno}: `.{node.attr}` — контейнер добывается из "
                    "запроса. Это service locator: объявляй зависимость параметром."
                )
            # Точечная форма `dishka.AsyncContainer` — то же самое имя, просто
            # через модуль; по ast.Name её не видно
            elif isinstance(node, ast.Attribute) and node.attr in local_types:
                errors.append(
                    f"{relative}:{node.lineno}: `{node.attr}` вне композиции — контейнер "
                    "не передаётся объекту, чтобы тот достал себе зависимости (правило 5)."
                )
            elif isinstance(node, ast.Name) and node.id in local_types:
                errors.append(
                    f"{relative}:{node.lineno}: `{node.id}` вне композиции — контейнер "
                    "не передаётся объекту, чтобы тот достал себе зависимости (правило 5)."
                )
    return errors


def check_router_injection(config: dict) -> list[str]:
    """Роутер со своими маршрутами обязан объявить класс маршрута.

    Без него `FromDishka` молча не сработает, и отказ наступит в проде как
    «параметр не пришёл». Проверяются только ЛИСТОВЫЕ роутеры: вниз класс
    не наследуется, агрегатору он бесполезен.
    """
    route_class = config.get("route_class")
    if not route_class:
        return []

    allowed = config.get("injection_allowed", [])
    methods = {"get", "post", "put", "patch", "delete", "head", "options"}
    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        # Модуль со своей композицией из контейнера ничего и не ждёт
        if _allowed(path, allowed):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        # Маршрут — это ДЕКОРАТОР, а не любое упоминание слова `get`: поиск по
        # атрибутам считал маршрутом `os.environ.get(...)`, и агрегатор с одним
        # `dict.get` объявлялся роутером без `route_class`.
        has_routes = any(
            isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Attribute)
            and deco.func.attr in methods
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            for deco in node.decorator_list
        )
        if not has_routes:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) != "APIRouter":
                continue
            # Точечная форма `mod.DishkaRoute` — тот же класс, просто через
            # модуль; сверка только по `ast.Name` объявляла её отсутствующей
            declared = any(
                kw.arg == "route_class"
                and (getattr(kw.value, "id", None) or getattr(kw.value, "attr", None))
                == route_class
                for kw in node.keywords
            )
            if not declared:
                relative = path.relative_to(ROOT).as_posix()
                errors.append(
                    f"{relative}:{node.lineno}: у роутера с маршрутами нет "
                    f"`route_class={route_class}` — подстановка из контейнера не сработает"
                )
    return errors


def check_no_repository_at_entry(config: dict) -> list[str]:
    """Вход не берёт репозиторий: он зовёт сценарий.

    Граница, которую `tach` провести не может: хендлер, забравший данные сам,
    проходит все проверки, и единственный статический признак — ЧТО он попросил
    у контейнера. Исключения для гейтов НЕТ: резолюция — общая половина.
    """
    entry_roots = config.get("entry_roots", [])
    if not entry_roots:
        return []
    # Опечатка в пути (`app/interfaces` вместо `app/interface`) давала пустой
    # обход и бодрое «OK»: проверка не проходила, а выглядела пройденной.
    for root in entry_roots:
        require_dir(ROOT / root, "[tool.composition].entry_roots")

    errors: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if not _allowed(path, entry_roots):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            for argument in arguments:
                annotation = argument.annotation
                # `FromDishka[T]` — Subscript, где срез и есть запрошенный тип
                if not isinstance(annotation, ast.Subscript):
                    continue
                if getattr(annotation.value, "id", None) != "FromDishka":
                    continue
                requested = ast.unparse(annotation.slice)
                # Предикат общий с `check_db_access` (`_project.names_repository`):
                # строгое окончание пропускало `SubscriptionRepoByPortal` и
                # `PortalAnchorTemplateRepoFactory` — они отдают репозиторий, и
                # вход, попросивший их, берёт репозиторий ровно так же.
                if not names_repository(requested):
                    continue
                relative = path.relative_to(ROOT).as_posix()
                errors.append(
                    f"{relative}:{node.lineno}: `{node.name}` просит "
                    f"`{requested}` — вход берёт репозиторий вместо сценария. "
                    "Данные достаёт application: сценарий или сервис-резолвер."
                )
    return errors


def main() -> int:
    config = _config()
    if not config:
        print("Composition: [tool.composition] не настроен — проверка пропущена.")
        return 0
    errors = (
        check_framework_injection(config)
        + check_router_injection(config)
        + check_no_service_locator(config)
        + check_no_repository_at_entry(config)
    )
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} нарушений границы внедрения.")
        print("Правило — docs/rules/композиция-и-скоупы.md")
        return 1
    print("Composition: зависимости приходят из контейнера, вход зовёт сценарий. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
