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
