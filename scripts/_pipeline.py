"""Индекс исходников для карты пайплайна: кто чем владеет и куда ведёт вызов.

Карта строится обходом вызовов от точки входа, и рвётся обход ровно в одном
месте — на порте: `self._index.search()` в сценарии указывает на `Protocol`, у
которого тела нет. Связь порта с реализацией структурная, по самим файлам её
не восстановить; зато композиция обязана назвать обе стороны явно, иначе не
соберётся граф, — оттуда и берём (`_providers.Bindings`).

Второе, что нужно обходу: чем объекту ВЛАДЕЮТ. Узлом становится только вызов
через то, что пришло в `__init__`, — критерий «что объекту дали», а не «что
модуль импортировал». Поэтому индекс держит поля классов, а не только методы.
"""

import ast
from pathlib import Path
from typing import NamedTuple

from _providers import Bindings, TypeNames


class Module(NamedTuple):
    """Разобранный файл: дерево, первая строка шапки и что откуда импортировано."""

    path: str
    tree: ast.Module
    doc: str
    imports: dict[str, tuple[str, str]]


class Owner(NamedTuple):
    """Класс, внутри которого идёт обход, и типы его полей."""

    name: str
    module: str
    node: ast.ClassDef | None
    fields: dict[str, str]


class Target(NamedTuple):
    """Куда привёл вызов через поле: тип по аннотации и реализация под ним."""

    port: str
    impl: str
    impl_module: str


class Signature:
    """Подпись метода для всплывающей подсказки: заголовок без тела."""

    @staticmethod
    def of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        try:
            args = ast.unparse(node.args)
        except Exception:  # pragma: no cover — на сломанном узле подсказка не важнее карты
            args = "..."
        tail = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"{prefix} {node.name}({args}){tail}"


class Fields:
    """Что объекту дали: `self._x` → имя типа.

    Смотрим аннотации `__init__` и присваивания в его теле. Аннотация поля на
    уровне класса тоже в счёт: так пишут при `dataclass`.
    """

    @staticmethod
    def of(node: ast.ClassDef) -> dict[str, str]:
        found: dict[str, str] = {}
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                found.update(Fields._single(statement.target.id, statement.annotation))
        for statement in node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef) and statement.name == "__init__":
                found.update(Fields._from_init(statement))
        return found

    @staticmethod
    def _single(name: str, annotation: ast.expr | None) -> dict[str, str]:
        names = TypeNames.result_of(annotation)
        return {name: next(iter(names))} if len(names) == 1 else {}

    @staticmethod
    def _from_init(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
        parameters: dict[str, str] = {}
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            parameters.update(Fields._single(argument.arg, argument.annotation))
        found: dict[str, str] = {}
        for statement in ast.walk(node):
            if isinstance(statement, ast.AnnAssign) and Fields._attribute(statement.target):
                found.update(Fields._single(str(Fields._attribute(statement.target)), statement.annotation))
            if not isinstance(statement, ast.Assign):
                continue
            attribute = next((Fields._attribute(t) for t in statement.targets if Fields._attribute(t)), None)
            if attribute and isinstance(statement.value, ast.Name) and statement.value.id in parameters:
                found[attribute] = parameters[statement.value.id]
        return found

    @staticmethod
    def _attribute(node: ast.expr) -> str:
        """`self._x` → `_x`; всё остальное — пусто."""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            return node.attr
        return ""


class Given:
    """Что дали аргументами: вход и вложенная функция получают зависимости так.

    Критерий тот же, что у полей класса, — «что объекту дали»; разница в том,
    что конструктора у модульной функции нет и зависимость приезжает
    аргументом либо обратным вызовом, который позовут за неё.
    """

    @staticmethod
    def of(node: ast.FunctionDef | ast.AsyncFunctionDef, source: "Source") -> dict[str, str]:
        found: dict[str, str] = {}
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            for name in TypeNames.result_of(argument.annotation):
                if source.owner(name) or name in source.bindings:
                    found[argument.arg] = name
                    break
        return found


class Source:
    """Разобранное дерево проекта: модули, классы, методы, проводка портов."""

    def __init__(self, root: Path, project_root: Path) -> None:
        self._project_root = project_root
        self.modules: dict[str, Module] = {}
        self._classes: dict[str, tuple[str, ast.ClassDef]] = {}
        self._functions: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self._ambiguous: set[str] = set()
        self.bindings: dict[str, tuple[str, str]] = {}
        for path in sorted(root.rglob("*.py")):
            self._add(path)
        self._collect_bindings()

    def _add(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
        except (SyntaxError, OSError, UnicodeDecodeError):
            return
        relative = path.relative_to(self._project_root).as_posix()
        doc = (ast.get_docstring(tree) or "").strip().splitlines()
        imports: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    # Ключ — имя НА МЕСТЕ ВЫЗОВА, значение — настоящее имя в чужом
                    # модуле: `import record as _record` иначе ищется как `_record`
                    # там, где объявлен `record`, и не находится никогда.
                    imports[alias.asname or alias.name] = (node.module.replace(".", "/") + ".py", alias.name)
        self.modules[relative] = Module(relative, tree, doc[0] if doc else "", imports)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self._functions[(relative, node.name)] = node
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name in self._classes:
                self._ambiguous.add(node.name)
            self._classes[node.name] = (relative, node)

    def _collect_bindings(self) -> None:
        """Порт → реализация. Кто кого отдаёт, знает только композиция.

        Разбор общий с `check_db_access`, выбор среди кандидатов — местный:
        реализация та, которую фабрика ВОЗВРАЩАЕТ, а не её аргументы. Ошибка
        здесь уводит обход в чужой класс молча — дерево всё равно построится.
        """
        candidates: dict[str, list[str]] = {}
        for relative, module in self.modules.items():
            returned = self._returned(module.tree)
            for binding in Bindings.of(module.tree, relative):
                if not binding.port or binding.impl not in self._classes:
                    continue
                found = candidates.setdefault(binding.port, [])
                found.insert(0, binding.impl) if binding.impl in returned else found.append(binding.impl)
        for port, found in candidates.items():
            # Порт среди кандидатов — значит класс отдают им же самим
            # (`provide(SearchUseCase)`), это не проводка: обход и так найдёт
            # его по имени. Записать сюда соседа значило бы подменить класс.
            if port in found:
                continue
            self.bindings[port] = (found[0], self._classes[found[0]][0])

    @staticmethod
    def _returned(tree: ast.Module) -> set[str]:
        """Классы, которые создают прямо в `return`/`yield`, а не по дороге."""
        found: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return | ast.Yield) or not isinstance(node.value, ast.Call):
                continue
            name = getattr(node.value.func, "id", "")
            if name:
                found.add(name)
        return found

    def owner(self, name: str) -> Owner | None:
        """Класс по имени. Одноимённые классы в разных слоях — отказ, а не догадка."""
        found = self._classes.get(name)
        if not found or name in self._ambiguous:
            return None
        module, node = found
        return Owner(name, module, node, Fields.of(node))

    def method(self, owner: Owner, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        """Метод класса или его базы, объявленной в том же проекте."""
        if owner.node is None:
            return None
        for statement in owner.node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef) and statement.name == name:
                return statement
        for base in owner.node.bases:
            names = TypeNames.of(base)
            parent = self.owner(next(iter(names))) if len(names) == 1 else None
            if parent and parent.name != owner.name:
                found = self.method(parent, name)
                if found is not None:
                    return found
        return None

    def function(self, name: str, module: str) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, str] | None:
        """Функция уровня модуля — своя или импортированная из этого же проекта.

        Границы объекта она не пересекает и узлом не становится, но работу
        делает: половина слоя входа живёт такими помощниками. Пропустить её —
        показать вход из одного шага там, где уходит запись события.
        """
        found = self._functions.get((module, name))
        if found is not None:
            return found, module
        source = self.modules.get(module)
        elsewhere = source.imports.get(name) if source else None
        if elsewhere and elsewhere in self._functions:
            return self._functions[elsewhere], elsewhere[0]
        return None

    def target(self, type_name: str) -> Target:
        """Тип поля → что за ним стоит на самом деле."""
        impl, impl_module = self.bindings.get(type_name, ("", ""))
        if not impl:
            known = self._classes.get(type_name)
            return Target("", type_name, known[0] if known else "")
        return Target(type_name, impl, impl_module)

    def doc(self, module: str) -> str:
        found = self.modules.get(module)
        return found.doc if found else ""
