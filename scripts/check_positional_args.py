"""Аргументов четыре и больше — называть по имени все.

`reporter.report(RequestOrigin.read(request), q, outcome, total_ms)` читается
только вместе с открытым рядом определением, а перепутанные местами соседи
одного типа не дают ни ошибки, ни красного теста: рабочий код с другим
поведением. Порог на ВСЕХ аргументах, а не на безымянных: `f(a, b, c, debug=1)`
— те же четыре значения, и три из них по-прежнему ребус.

Бьётся только то, чему имя дать можно: вызываемое лежит в проекте, и аргумент
попадает в именуемый параметр. `logger.info("{} {} {} {}", a, b, c, d)` и
прочие `*args` из чужих библиотек правилу не подчиняются — имени там нет, и
отказ означал бы «перепиши то, что переписать нельзя».

Пороги и корни — `[tool.positional_args]`, инлайновой пометки-побега нет.
"""

import ast
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural, require_dir, tool_config  # noqa: E402
from _symbols import Constructor, Definition, Modules, TypeName  # noqa: E402

_BOUND = {"self", "cls"}


class Params(NamedTuple):
    """Сколько параметров безымянны по определению и сколько имя принимают."""

    posonly: int
    nameable: int


class Where(NamedTuple):
    """Что видно на месте вызова: свой класс, типы полей и типы имён."""

    module: str
    enclosing: ast.ClassDef | None
    attributes: dict[str, str]
    names: dict[str, str]


class Signature:
    """Именуемая часть сигнатуры вызываемого."""

    @staticmethod
    def _of_function(node: ast.FunctionDef | ast.AsyncFunctionDef, bound: bool) -> Params:
        posonly = [a.arg for a in node.args.posonlyargs]
        named = [a.arg for a in node.args.args]
        if bound:
            if posonly and posonly[0] in _BOUND:
                posonly = posonly[1:]
            elif named and named[0] in _BOUND:
                named = named[1:]
        return Params(len(posonly), len(named))

    @staticmethod
    def method(module: str, node: ast.ClassDef, name: str) -> tuple[str, Definition] | None:
        """Метод класса или его предка: порт объявляет, адаптер повторяет."""
        for inner in node.body:
            if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef) and inner.name == name:
                return module, inner
        for base in node.bases:
            found = Modules.resolve(module, TypeName.of(base))
            if found is None:
                continue
            where, parent = found
            if isinstance(parent, ast.ClassDef):
                deeper = Signature.method(where, parent, name)
                if deeper is not None:
                    return deeper
        return None

    @staticmethod
    def _fields(node: ast.ClassDef) -> Params:
        """Носитель данных без `__init__`: конструктор собран из полей."""
        fields = [
            inner
            for inner in node.body
            if isinstance(inner, ast.AnnAssign) and isinstance(inner.target, ast.Name)
        ]
        return Params(0, len(fields))

    @staticmethod
    def _of_class(module: str, node: ast.ClassDef) -> Params:
        own = Signature.method(module, node, "__init__")
        if own is not None:
            where, init = own
            if not isinstance(init, ast.ClassDef):
                return Signature._of_function(init, bound=True)
        return Signature._fields(node)

    @staticmethod
    def of(module: str, node: Definition, bound: bool) -> Params:
        if isinstance(node, ast.ClassDef):
            return Signature._of_class(module, node)
        return Signature._of_function(node, bound)


class Callee:
    """Кого зовут. Разрешается только то, что видно статически, без догадок."""

    @staticmethod
    def module_of(path: Path) -> str:
        return path.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")

    @staticmethod
    def _owner(where: Where, value: ast.expr) -> str:
        """Чей это метод: имя класса, тип поля или тип переменной."""
        if isinstance(value, ast.Name):
            return where.names.get(value.id, value.id)
        if (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == "self"
        ):
            return where.attributes.get(value.attr, "")
        return ""

    @staticmethod
    def of(where: Where, func: ast.expr) -> tuple[str, Definition, bool] | None:
        if isinstance(func, ast.Name):
            found = Modules.resolve(where.module, func.id)
            return (*found, False) if found else None
        if not isinstance(func, ast.Attribute):
            return None
        if isinstance(func.value, ast.Name) and func.value.id == "self":
            own = Signature.method(where.module, where.enclosing, func.attr) if where.enclosing else None
            return (*own, True) if own else None
        owner = Callee._owner(where, func.value)
        holder = Modules.resolve(where.module, owner) if owner else None
        if holder is None or not isinstance(holder[1], ast.ClassDef):
            return None
        found = Signature.method(holder[0], holder[1], func.attr)
        return (*found, True) if found else None


class Scope(ast.NodeVisitor):
    """Вызовы файла вместе с тем, что о типах известно на их месте."""

    def __init__(self, module: str) -> None:
        self.where = Where(module, None, {}, {})
        self.found: list[tuple[ast.Call, Where]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        saved = self.where
        self.where = saved._replace(
            enclosing=node, attributes={**saved.attributes, **Constructor.attribute_types(node)}
        )
        self.generic_visit(node)
        self.where = saved

    def _enter_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        saved = self.where
        declared = {
            argument.arg: TypeName.of(argument.annotation)
            for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            if argument.annotation is not None
        }
        # Имена соседней функции сюда не доезжают: одноимённая переменная с
        # другим типом увела бы резолюцию в чужой класс.
        self.where = saved._replace(names=declared)
        self.generic_visit(node)
        self.where = saved

    visit_FunctionDef = _enter_function  # noqa: N815
    visit_AsyncFunctionDef = _enter_function  # noqa: N815

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if isinstance(node.target, ast.Name):
            self.where.names[node.target.id] = TypeName.of(node.annotation)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self.found.append((node, self.where))
        self.generic_visit(node)


class Ribbons:
    """Ленты безымянных значений на местах вызова."""

    @staticmethod
    def _unnamed(passed: int, params: Params) -> int:
        """Сколько переданного позиционно легло в параметры, имеющие имя."""
        return min(max(passed - params.posonly, 0), params.nameable)

    @staticmethod
    def _of_call(call: ast.Call, where: Where, limit: int, exempt: set[str]) -> str:
        # Распаковка делает число аргументов неизвестным на статике, а порог
        # считается именно по числу: гадать тут — заводить ложные отказы.
        if any(isinstance(a, ast.Starred) for a in call.args):
            return ""
        if any(keyword.arg is None for keyword in call.keywords):
            return ""
        total = len(call.args) + len(call.keywords)
        if total < limit or not call.args:
            return ""
        shown = ast.unparse(call.func)
        if shown in exempt or shown.split(".")[-1] in exempt:
            return ""
        found = Callee.of(where, call.func)
        if found is None:
            return ""
        module, node, bound = found
        unnamed = Ribbons._unnamed(len(call.args), Signature.of(module, node, bound))
        if not unnamed:
            return ""
        return (
            f"{call.lineno}: `{shown}(...)` — аргументов {total}, из них "
            f"{plural(unnamed, 'безымянный', 'безымянных', 'безымянных')}. "
            f"От {limit} аргументов имя нужно каждому: перепутанные местами соседи "
            f"одного типа не дадут ни ошибки, ни красного теста."
        )

    @staticmethod
    def violations(roots: list[str], limit: int, exempt: set[str]) -> list[str]:
        errors: list[str] = []
        for root in roots:
            for path in sorted((ROOT / root).rglob("*.py")):
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                except (SyntaxError, OSError):
                    continue
                walker = Scope(Callee.module_of(path))
                walker.visit(tree)
                relative = path.relative_to(ROOT).as_posix()
                for call, where in walker.found:
                    message = Ribbons._of_call(call, where, limit, exempt)
                    if message:
                        errors.append(f"{relative}:{message}")
        return errors


def main() -> int:
    config = tool_config("positional_args")
    if not config:
        print("Positional args: [tool.positional_args] не настроен — проверка пропущена.")
        return 0
    roots = config.get("roots", [])
    limit = int(config.get("names_required_from", 4))
    if not roots:
        return 0
    for root in roots:
        require_dir(ROOT / root, "[tool.positional_args].roots")

    errors = Ribbons.violations(roots, limit, set(config.get("exempt", [])))
    if errors:
        for error in errors:
            print(error)
        print(f"\n{plural(len(errors), 'вызов', 'вызова', 'вызовов')} с безымянными аргументами.")
        print(f"От {limit} аргументов зови по имени: `report(origin=..., query=..., ms=...)`.")
        return 1
    print("Positional args: длинные вызовы зовут по имени. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
