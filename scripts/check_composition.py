"""Guard the injection boundary: зависимости приходят из контейнера, не из фреймворка.

Правило целиком — `docs/rules/композиция-и-скоупы.md`. Здесь проверяется
ровно один его пункт, потому что остальные закрыты инструментами, которые уже
есть:

* «инфраструктура собирается только в композиции» — это `tach`: он запрещает
  сам ИМПОРТ, а собрать то, что нельзя импортировать, невозможно. Список
  классов вести не надо, переименования не ломают, новый класс попадает под
  правило сам. Перечисление имён было бы денилистом, а денилисты текут.
* «процессный объект не зависит от привходового» — это контейнер: такой граф
  просто не соберётся.

А вот `Depends` через `tach` не выразить: он приходит из `fastapi`, который
роутам нужен и так — для `@router.get`. Разделить «импортирую фреймворк ради
маршрута» и «импортирую ради внедрения» можно только по месту вызова.

Проверка нужна не против человека, а против ИДИОМЫ. Агенту говорят «добавь
эндпоинт, ему нужен репозиторий планов» — он пишет `Depends(get_plan_repo)`,
потому что так написаны все примеры FastAPI на свете, и про контейнер он не
знает. Отказ на lint-check превращает молчаливый дрейф обратно к двум DI в
громкий.

Белый список — `[tool.composition].injection_allowed` в pyproject.toml. Там
только верификаторы входа: выбор способа проверки принадлежит маршрутизации,
а не контейнеру (пункт 3 правила).
"""

import ast
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, names_repository, source_root  # noqa: E402

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

    Правило 5 запрещает это прямым текстом, но ни одна проверка на это не
    смотрела: `Depends` тут нет, инфраструктура не импортируется, `tach`
    доволен. А вред тот же, что у второго DI — на месте вызова не видно, что
    объекту на самом деле нужно.

    Ловим два входа: контейнер как параметр (`FromDishka[AsyncContainer]`) и
    доставание его из состояния запроса (`request.state.dishka_container`).
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

    Без него `FromDishka` в хендлере молча не сработает: подстановку делает
    именно класс маршрута (либо декоратор на каждом хендлере, но тогда его
    забудут). Отказ при этом наступит в проде и будет выглядеть как «параметр
    не пришёл», а не как ошибка сборки.

    Проверяются только ЛИСТОВЫЕ роутеры — те, у кого есть собственные
    `@router.<метод>`. Агрегаторам класс маршрута бесполезен: `include_router`
    сохраняет класс ребёнка, вниз он не наследуется.
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
        # Маршрут — это ДЕКОРАТОР `@x.get(...)`, а не любое упоминание слова
        # `get`. Поиск по всем атрибутам считал маршрутом `os.environ.get(...)`
        # и `session.delete(...)`, из-за чего агрегатор с одним `dict.get`
        # объявлялся «роутером без route_class» — ложный отказ на законном
        # коде, а такие и приводят к отключению проверки целиком.
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

    Граница слоёв, которую `tach` провести не может. Импорт порта во входе
    легален — вход обязан на чём-то объявлять зависимости, — и хендлер,
    забравший данные сам и сложивший ответ, проходит все проверки: слой не
    нарушен, маршрут объявлен, `Depends` не использован. Единственный видимый
    статически признак — ЧТО именно он попросил у контейнера.

    Отсюда же и вероятность: «добавь ручку, ей нужен материал» — самый
    короткий путь именно этот, и он выглядит работающим.

    Исключения для гейтов НЕТ, хотя соблазн есть: гейт тоже ищет по токену.
    Но правило composition делит иначе — «проверка транспортная, резолюция
    общая». Прочитать заголовок — работа входа и у каждого входа своя.
    Превратить предъявленное в арендатора — общая половина: у очереди то же
    доказательство приедет в `message.kwargs`, и резолюция, осевшая в
    HTTP-гейте, окажется либо продублирована, либо вызвана оттуда, где нет
    `Request`. Значит и гейт просит не репозиторий, а резолвер из
    `application/`.
    """
    entry_roots = config.get("entry_roots", [])
    if not entry_roots:
        return []

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
