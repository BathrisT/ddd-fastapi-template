"""У всего, что вход просит через `FromDishka`, есть фабрика в контейнере.

Это то, чего НЕ делает валидация графа при `make_async_container`: она ходит
только по зарегистрированным фабрикам. `FromDishka[X]` в сигнатуре обработчика
в граф не входит — «вход просит то, чего в контейнере нет» проходит сборку
молча и падает в проде при первом обращении, ровно как забытый импорт, от
которого уходили явным реестром.

Проверяется наличие ФАБРИКИ, а не результат сборки: собирать по-настоящему
означало бы поднять БД и Redis, то есть превратить сторожа раскладки в
интеграционный тест. Отсутствие фабрики — ровно тот отказ, который надо ловить.
"""

from __future__ import annotations

import ast
import inspect
import sys
from typing import Any

import pytest
from dishka import AsyncContainer
from fastapi.routing import APIRoute
from taskiq import InMemoryBroker

from app.composition.container import AppContainer
from app.composition.worker_tasks import WorkerTasks
from app.config import LLM, App, Database, Redis, Settings
from app.interface.api.routes import router as api_router

# Ниже этого числа проверка считается сломанной, а не «всё хорошо». Пустой
# список параметров pytest молча превращает в SKIPPED, поэтому обвал сборщика
# (обёртки FastAPI недокументированы и могут поменяться) выглядел бы как
# зелёный прогон. Держится близко к фактическому: запас в полсотни означал бы,
# что все проверки входа могут выпасть из сборщика незаметно.
_MIN_EXPECTED_REQUESTS = 5


def _settings() -> Settings:
    return Settings(
        app=App(
            env="development",
            api_base_url="http://localhost:8000",
            frontend_base_url="http://localhost:5173",
            fernet_key="dGVzdC1mZXJuZXQta2V5LTMyLWJ5dGVzLWxvbmchISE=",
        ),
        database=Database(host="localhost", port=5432, user="u", password="p", name="n"),
        redis=Redis(),
        llm=LLM(api_key="test-key"),
    )


def _requested_types(func: Any) -> list[Any]:
    """Типы, которые функция просит у контейнера через `FromDishka[T]`.

    Читается ИСХОДНИК, а не сигнатура объекта, и это принципиально: и
    `DishkaRoute`, и `@inject` оборачивают функцию, ВЫРЕЗАЯ параметры
    `FromDishka` из сигнатуры, а `__wrapped__` при этом не выставляют. По
    живому объекту зависимостей не видно вовсе — проверка молча отдавала бы
    пустой список для каждого маршрута.
    """
    module = sys.modules.get(getattr(func, "__module__", ""))
    name = getattr(func, "__name__", "")
    if module is None or not name:
        return []
    try:
        tree = ast.parse(inspect.getsource(module))
    except (OSError, SyntaxError, TypeError):  # pragma: no cover — исходник недоступен
        return []

    requested: list[Any] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or node.name != name:
            continue
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            annotation = arg.annotation
            # `FromDishka[T]` — Subscript, где срез и есть искомый тип
            if not isinstance(annotation, ast.Subscript):
                continue
            if getattr(annotation.value, "id", None) != "FromDishka":
                continue
            resolved = getattr(module, ast.unparse(annotation.slice), None)
            if resolved is not None:
                requested.append(resolved)
    return requested


def _known_types(container: AsyncContainer) -> set[Any]:
    """Всё, для чего в контейнере есть фабрика — по цепочке скоупов вниз."""
    known: set[Any] = set()
    registry = container.registry
    while registry is not None:
        known.update(key.type_hint for key in registry.factories)
        registry = registry.child_registry
    return known


def _walk(router: Any) -> tuple[list[APIRoute], list[Any]]:
    """Маршруты дерева и ПРОВЕРКИ, объявленные на роутерах.

    Проверки собираются отдельно и это не педантизм: они подключаются
    `dependencies=[Depends(...)]` на роутере, поэтому в `route.dependant`
    голого агрегатора их нет, а `app.routes` разворачивается лениво и прямой
    фильтрацией даёт ноль.
    """
    routes: list[APIRoute] = []
    guards: list[Any] = list(getattr(router, "dependencies", []) or [])
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            routes.append(route)
            guards.extend(route.dependant.dependencies)
        elif hasattr(route, "original_router"):
            nested_routes, nested_guards = _walk(route.original_router)
            routes.extend(nested_routes)
            guards.extend(nested_guards)
            guards.extend(getattr(route.include_context, "dependencies", []) or [])
        elif hasattr(route, "routes"):
            nested_routes, nested_guards = _walk(route)
            routes.extend(nested_routes)
            guards.extend(nested_guards)
    return routes, guards


def _callable_of(dependency: Any) -> Any:
    """И `Depends(...)`, и уже разобранная зависимость FastAPI."""
    return getattr(dependency, "dependency", None) or getattr(dependency, "call", None)


def _entry_requests() -> list[tuple[str, Any]]:
    """Всё, что просят входы: обработчики очереди, хендлеры и проверки входа."""
    routes, guards = _walk(api_router)
    entries = [(h.__name__, h) for h in WorkerTasks.TABLE]
    entries += [(route.name, route.endpoint) for route in routes]
    entries += [(call.__name__, call) for call in map(_callable_of, guards) if call is not None]
    return [(owner, requested) for owner, func in entries for requested in _requested_types(func)]


_REQUESTS = _entry_requests()


def test_collector_still_sees_the_entry_points() -> None:
    """Сборщик не должен молча выключиться.

    Пустой список параметров pytest превращает в SKIPPED, а обёртки FastAPI и
    dishka недокументированы: перестанут разворачиваться — все проверки станут
    нулём, и прогон останется зелёным.
    """
    assert len(_REQUESTS) >= _MIN_EXPECTED_REQUESTS


@pytest.mark.parametrize(
    ("owner", "requested"),
    _REQUESTS,
    ids=lambda v: v if isinstance(v, str) else getattr(v, "__name__", str(v)),
)
def test_entry_dependency_has_a_factory(owner: str, requested: Any) -> None:
    container = AppContainer.build(_settings(), InMemoryBroker())
    if requested not in _known_types(container):
        pytest.fail(f"{owner} просит {requested}, но фабрики в контейнере нет")
