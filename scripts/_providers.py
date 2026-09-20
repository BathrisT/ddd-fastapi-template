"""Что умеет собирать контейнер: один разбор провайдеров на всех сторожей.

Список внедряемого объявлен явно в композиции — туда и смотрим, вместо того
чтобы угадывать по имени или по папке. Разбор общий, потому что два разбора
одного и того же однажды разойдутся в понимании, что такое биндинг, и не
сообщат об этом ни один.

Формы записи, которые понимает dishka, и все они встречаются в шаблоне:

    users = provide(SqlUserRepo, provides=UserRepo)       # обе стороны
    @provide
    def cipher(self, settings: Settings) -> TokenCipher   # по типу результата
    settings = from_context(provides=Settings, ...)       # эту забывают чаще всего

Возвращается не только порт, но и реализация: контейнер умеет выдать и её.
"""

import ast
from pathlib import Path
from typing import NamedTuple

CALLS = {"provide", "provide_all", "alias", "from_context"}
KEYWORDS = {"provides", "source"}

# `@provide async def redis(...) -> AsyncIterator[Redis]` отдаёт Redis, а не
# итератор: оборачивают его ради закрытия ресурса на выходе.
WRAPPERS = {"AsyncIterator", "Iterator", "AsyncGenerator", "Generator", "AsyncContextManager"}


class TypeNames:
    """Имена типов внутри аннотации: `X | None`, `list[X]` → {X}."""

    @staticmethod
    def of(node: ast.expr | None) -> set[str]:
        if node is None:
            return set()
        if isinstance(node, ast.Name):
            return {node.id}
        if isinstance(node, ast.Attribute):
            return {node.attr}
        if isinstance(node, ast.Subscript):
            return TypeNames.of(node.value) | TypeNames.of(node.slice)
        if isinstance(node, ast.BinOp):
            return TypeNames.of(node.left) | TypeNames.of(node.right)
        if isinstance(node, ast.Tuple):
            return {name for element in node.elts for name in TypeNames.of(element)}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                return TypeNames.of(ast.parse(node.value, mode="eval").body)
            except SyntaxError:
                return set()
        return set()

    @staticmethod
    def result_of(node: ast.expr | None) -> set[str]:
        """То же, но с распаковкой обёрток ресурса."""
        if isinstance(node, ast.Subscript) and TypeNames.of(node.value) & WRAPPERS:
            return TypeNames.result_of(node.slice)
        return TypeNames.of(node)


class Built:
    """Типы, которые контейнер умеет выдать, и место, где это объявлено."""

    @staticmethod
    def _from_call(node: ast.Call) -> set[str]:
        names: set[str] = set()
        for argument in node.args:
            names |= TypeNames.of(argument)
        for keyword in node.keywords:
            if keyword.arg in KEYWORDS:
                names |= TypeNames.of(keyword.value)
        return names

    @staticmethod
    def _decorated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if TypeNames.of(target) & CALLS:
                return True
        return False

    @staticmethod
    def types(roots: list[Path]) -> dict[str, str]:
        """`{имя типа: файл:строка}` по всем провайдерам проекта."""
        found: dict[str, str] = {}
        for root in roots:
            for path in sorted(root.rglob("*.py")):
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                except (SyntaxError, OSError):
                    continue
                for node in ast.walk(tree):
                    names: set[str] = set()
                    if isinstance(node, ast.Call) and TypeNames.of(node.func) & CALLS:
                        names = Built._from_call(node)
                    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                        if Built._decorated(node):
                            names = TypeNames.result_of(node.returns)
                    for name in names:
                        found.setdefault(name, f"{path.name}:{node.lineno}")
        return found


class Binding(NamedTuple):
    """Что композиция отдаёт под портом и где она это сказала."""

    impl: str
    impl_module: str
    port: str
    port_module: str
    where: str
    line: int


class Bindings:
    """Порт встречается со своей реализацией.

    Связь структурная, по самим файлам их не сопоставить; композиция же обязана
    назвать обе стороны явно, иначе не соберётся граф. Разбор общий с
    `check_db_access`: два разбора однажды разойдутся и не скажут об этом.
    """

    @staticmethod
    def _import_sources(tree: ast.Module) -> dict[str, str]:
        sources: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    sources[alias.asname or alias.name] = node.module
        return sources

    @staticmethod
    def _single(names: set[str]) -> str:
        """Порт — одно имя. `X | None` портом не объявляют, и гадать тут нечего."""
        return next(iter(names)) if len(names) == 1 else ""

    @staticmethod
    def _built_in_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        """Кого фабрика создаёт или пропускает через себя.

        Два способа, и оба живые: `return SqlCommitter(session)` — создаёт,
        `return client` при `client: TaskiqTaskQueue` — псевдоним, когда один
        объект отдают под двумя портами.
        """
        created = [
            name
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call)
            for name in [getattr(inner.func, "id", "")]
            if name
        ]
        arguments = {
            argument.arg: TypeNames.of(argument.annotation)
            for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        }
        passed = [
            name
            for inner in ast.walk(node)
            if isinstance(inner, ast.Return | ast.Yield) and isinstance(inner.value, ast.Name)
            for name in arguments.get(inner.value.id, set())
        ]
        return created + passed

    @staticmethod
    def of(tree: ast.Module, relative: str) -> list[Binding]:
        sources = Bindings._import_sources(tree)
        found: list[Binding] = []

        def add(impl: str, port: str, line: int) -> None:
            found.append(
                Binding(
                    impl, sources.get(impl, ""), port, sources.get(port, ""), relative, line
                )
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "provide":
                impl = getattr(node.args[0], "id", "") if node.args else ""
                port = Bindings._single(
                    {
                        name
                        for keyword in node.keywords
                        if keyword.arg == "provides"
                        for name in TypeNames.of(keyword.value)
                    }
                )
                if impl:
                    add(impl, port, node.lineno)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns:
                # Фабрика — помеченная декоратором. Без этого приватный хелпер
                # рядом с провайдером считался бы биндингом, и сторож упал бы
                # на коде, к графу зависимостей не относящемся.
                if not Built._decorated(node):
                    continue
                port = Bindings._single(TypeNames.result_of(node.returns))
                for impl in Bindings._built_in_body(node):
                    add(impl, port, node.lineno)
        return found
