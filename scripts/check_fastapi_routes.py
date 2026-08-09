"""Правила слоя входа HTTP: в `routes/` живут маршруты и ничего кроме.

Проверка белого списка, а не чёрного, и это принципиально. Запрет «в routes не
должно быть классов» ловит ровно классы: завтра туда положат функцию-помощника,
разбор тела или константу с бизнес-правилом — и запрет промолчит. Требование
«каждый файл объявляет хотя бы один маршрут» ловит любого чужака сразу, потому
что чужак маршрутов не объявляет.

Что отсюда уже уехало: разбор события бот-вебхука и разбор загруженного файла —
оба знали про проводной формат, оба были нужны двум роутерам сразу и оба
лежали в `routes/` только потому, что там их написали. Теперь `parsing/`.

Схемы запроса и ответа под правило НЕ попадают: pydantic-модель рядом со своим
маршрутом читается лучше, чем в файле через две папки, и чужаком не является —
она описывает тот же самый маршрут.

Конфиг — `[tool.fastapi_routes]` в pyproject.toml.
"""

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import require_dir  # noqa: E402


def _config() -> dict:
    raw = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return raw.get("tool", {}).get("fastapi_routes", {})


def _declares_route(tree: ast.AST, methods: set[str]) -> bool:
    """Есть ли хоть один `@<что-то>.<метод>(...)` на функции."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if isinstance(func, ast.Attribute) and func.attr in methods:
                return True
    return False


def check_routes_only_declare_routes(config: dict) -> list[str]:
    roots = config.get("route_dirs", [])
    methods = set(config.get("http_methods", []))
    if not roots or not methods:
        return []

    errors: list[str] = []
    for root in roots:
        base = require_dir(ROOT / root, "[tool.fastapi_routes].route_dirs")
        for path in sorted(base.rglob("*.py")):
            # Агрегаторы собирают роутеры через include_router и своих
            # маршрутов не объявляют — это их работа, а не чужеродность
            if path.name == "__init__.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            if _declares_route(tree, methods):
                continue
            relative = path.relative_to(ROOT).as_posix()
            errors.append(
                f"{relative}: файл в `routes/` не объявляет ни одного маршрута. "
                "Здесь живут только эндпоинты (и схемы их запроса/ответа); "
                "разбор тела — в `parsing/`, проверка входа — в `guards/`, "
                "логика — в `application/`."
            )
    return errors


def check_no_deferred_annotations(config: dict) -> list[str]:
    """Модуль с маршрутами не откладывает аннотации.

    `from __future__ import annotations` превращает `-> XResponse` в СТРОКУ.
    Пока хендлер голый, фреймворк резолвит её по `__globals__` модуля и всё
    сходится; но хендлер обёрнут (внедрение зависимостей, трассировка,
    ретраи), и `__globals__` у обёртки уже её собственные — имени схемы там
    нет. Модель ответа остаётся нерезолвнутым ForwardRef.

    Отказ при этом максимально поздний и тихий: приложение стартует, схема
    OpenAPI рисуется, тесты зелёные — и маршрут отдаёт 500 на сериализации
    ОТВЕТА, когда сценарий уже отработал. Так слегло двадцать маршрутов
    разом, и увидели это только по логу прода.

    Почему запрет, а не проверка «модель ответа собирается»: собрать
    приложение из скрипта значит поднять весь граф зависимостей с настройками
    и сетью. А после снятия этого импорта остальные способы сломать модель
    ответа (схема объявлена ниже хендлера, импортирована под `TYPE_CHECKING`)
    падают `NameError` сразу при импорте модуля — то есть громко и на старте.
    Тихий способ ровно один, и он здесь.
    """
    roots = config.get("route_dirs", [])
    methods = set(config.get("http_methods", []))
    if not roots or not methods:
        return []

    errors: list[str] = []
    for root in roots:
        base = require_dir(ROOT / root, "[tool.fastapi_routes].route_dirs")
        for path in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            if not _declares_route(tree, methods):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module != "__future__":
                    continue
                if not any(alias.name == "annotations" for alias in node.names):
                    continue
                relative = path.relative_to(ROOT).as_posix()
                errors.append(
                    f"{relative}:{node.lineno}: `from __future__ import annotations` в модуле "
                    "с маршрутами. Аннотация ответа станет строкой, и обёрнутый хендлер "
                    "не даст её резолвить — маршрут отдаст 500 на сериализации ответа, "
                    "а не при старте."
                )
    return errors


def main() -> int:
    config = _config()
    if not config:
        print("FastAPI routes: [tool.fastapi_routes] не настроен — проверка пропущена.")
        return 0
    errors = check_routes_only_declare_routes(config) + check_no_deferred_annotations(config)
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} нарушений в слое маршрутов.")
        return 1
    print("FastAPI routes: в routes/ только маршруты, аннотации не отложены. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
