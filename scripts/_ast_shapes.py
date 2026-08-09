"""Общий разбор формы класса для сторожей раскладки.

Лежит отдельным модулем, потому что «что считать носителем данных» — один
вопрос, а отвечали на него две копии в `check_class_shape` и `check_use_cases`.
Копии успели разойтись ровно в том месте, где это было важно (см. ниже).
"""

import ast
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import source_root  # noqa: E402

# Носитель данных по декоратору. `dataclass` здесь НЕТ намеренно: его наличие
# ничего не говорит о том, есть ли у класса поведение, — см. `is_data_carrier`.
DATA_DECORATORS = {"define", "frozen", "attrs", "attr_s"}

DATA_BASES = {
    "BaseModel",
    "BaseSettings",
    "Enum",
    "IntEnum",
    "StrEnum",
    "Flag",
    "IntFlag",
    "TypedDict",
    "NamedTuple",
    "Protocol",
    "Exception",
    "BaseException",
    "Base",
    "DeclarativeBase",
    "TypeDecorator",
}

# Базы, у которых носителем данных становится и НАСЛЕДНИК: форма, описанная
# полями. Протоколов, ORM-баз и исключений здесь нет намеренно — см.
# `carrier_names`.
DATA_ONLY_BASES = {
    "BaseModel",
    "BaseSettings",
    "Enum",
    "IntEnum",
    "StrEnum",
    "Flag",
    "IntFlag",
    "TypedDict",
    "NamedTuple",
}


@lru_cache(maxsize=1)
def carrier_names() -> frozenset[str]:
    """Классы `app/`, чья база — форма данных, на любой глубине наследования.

    Без этого прохода правило смотрит только на ПРЯМУЮ базу, и
    `class SurveyEditArgs(SurveyArgs)` читается поведенческим: `SurveyArgs` в
    `DATA_BASES` не значится, хотя сам наследует `BaseModel`. Формы аргументов
    в паре «завести новое / переписать существующее» отличаются ровно одним
    полем, и запретить им наследование значило бы требовать копию всех полей
    вместе с валидаторами — то есть чинить настоящую беду выдуманной.

    Наследуется только от `DATA_ONLY_BASES`, а не от всего `DATA_BASES`.
    Разница принципиальная: `Protocol` в списке носителей стоит потому, что
    ОБЪЯВЛЕНИЕ протокола — это описание, но НАСЛЕДОВАНИЕ протоколу — ровно
    способ реализовать поведение. Пусти замыкание по нему — и первый же
    `class SqlXRepo(XRepo)` перестанет считаться поведенческим сразу для двух
    сторожей, включая `strict`-режим, заведённый против маскировки сценариев.
    То же у ORM-`Base` и исключений.

    Имена, а не пути: сторожа и так работают по именам баз, а разные классы с
    одинаковым именем в `app/` — сами по себе повод для разговора.
    """
    bases_of: dict[str, set[str]] = {}
    for path in source_root().rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases_of.setdefault(node.name, set()).update(base_names(node))

    carriers: set[str] = set()
    # До неподвижной точки: наследник наследника тоже носитель, а цикл в
    # объявлениях (его дал бы только битый код) просто перестанет добавлять новое
    while True:
        found = {
            name
            for name, bases in bases_of.items()
            if name not in carriers and bases & (DATA_ONLY_BASES | carriers)
        }
        if not found:
            return frozenset(carriers)
        carriers |= found


def decorator_names(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Имена декораторов; `@x` и `@x(...)` — одно и то же."""
    names = set()
    for deco in node.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        name = getattr(target, "id", None) or getattr(target, "attr", None)
        if name:
            names.add(name)
    return names


def base_names(node: ast.ClassDef) -> set[str]:
    """Имена баз; `Protocol[T]` (ast.Subscript) отдаёт `Protocol`.

    Разворачивать Subscript обязательно: без этого дженерик-протокол
    `class X(Protocol[T])` не опознаётся как протокол вообще — оба `getattr`
    по подписке дают None, и правило «Protocol только в ports/» молчит.
    """
    names = set()
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        name = getattr(target, "id", None) or getattr(target, "attr", None)
        if name:
            names.add(name)
    return names


def behaviour_methods(node: ast.ClassDef) -> list[str]:
    """Методы, которые несут поведение: не дандеры и не объявления протокола."""
    return [
        item.name
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        and not (item.name.startswith("__") and item.name.endswith("__"))
    ]


def is_data_carrier(node: ast.ClassDef, *, strict: bool = False) -> bool:
    """Класс описывает данные, а не поведение.

    `strict` управляет единственной спорной формой — `@dataclass` с методами.

    Обычный режим засчитывает её носителем: сущность с полями и своими
    инвариантами (`Goal.is_completed`, `Block.start`) — это ровно то, для чего
    dataclass и нужен, и держать двух таких соседок в одном файле моделей
    нормально.

    Строгий режим — для каталогов, где по определению не бывает носителей
    данных с поведением: каталог сценариев, каталог обработчиков. Там
    `@dataclass` на классе с методом `execute` не описывает данные, а прячет
    сценарий от проверки. Без строгого режима так и жили три сценария подряд,
    которых не проверял никто.
    """
    decorators = decorator_names(node)
    if decorators & DATA_DECORATORS:
        return True
    if "dataclass" in decorators:
        return not (strict and behaviour_methods(node))
    # Примесь несёт поля, а не поведение — и по определению живёт рядом с тем,
    # во что примешивается
    if node.name.endswith("Mixin"):
        return True
    bases = base_names(node)
    if bases & (DATA_BASES | carrier_names()):
        return True
    return any(b.endswith(("Error", "Exception", "Mixin")) for b in bases)
