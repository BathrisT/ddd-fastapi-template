"""Где объявлено имя: один разбор дерева на всех сторожей.

`app.x.y` → файл → определение в нём. Разбор общий по той же причине, по
которой общий разбор провайдеров: два обхода одного и того же однажды
разойдутся в понимании, что считать определением, и не сообщат об этом ни один.

Резолюция идёт по импортам файла, а не по имени класса в куче: одноимённые
классы в разных модулях иначе слипаются, и сторож судит не о том файле.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT  # noqa: E402

Definition = ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


class Modules:
    """Дерево модуля проекта и объявленные в нём имена."""

    @staticmethod
    def tree(module: str) -> ast.Module | None:
        if not module:
            return None
        path = ROOT / (module.replace(".", "/") + ".py")
        if not path.is_file():
            path = ROOT / module.replace(".", "/") / "__init__.py"
        try:
            return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, OSError):
            return None

    @staticmethod
    def imports(module: str) -> dict[str, str]:
        """`{локальное имя: модуль, откуда ввезено}` — с учётом псевдонимов."""
        tree = Modules.tree(module)
        if tree is None:
            return {}
        return {
            alias.asname or alias.name: node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names
        }

    @staticmethod
    def definition(module: str, name: str) -> Definition | None:
        """Объявление верхнего уровня в самом модуле."""
        tree = Modules.tree(module)
        if tree is None:
            return None
        for node in tree.body:
            if isinstance(node, Definition) and node.name == name:
                return node
        return None

    @staticmethod
    def resolve(module: str, name: str) -> tuple[str, Definition] | None:
        """Где на самом деле живёт имя, видимое в модуле: у себя или у соседа."""
        here = Modules.definition(module, name)
        if here is not None:
            return module, here
        source = Modules.imports(module).get(name, "")
        there = Modules.definition(source, name)
        if there is not None:
            return source, there
        return None


class TypeName:
    """Имя типа из аннотации: `FromDishka[Reporter]`, `X | None` -> `Reporter`, `X`.

    Обёртку снимаем, а не перечисляем: `FromDishka`, `Annotated`, `Final` и
    прочее приходит из разных библиотек, и список устаревал бы молча.
    """

    @staticmethod
    def of(node: ast.expr | None) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript):
            inner = node.slice
            if isinstance(inner, ast.Tuple):
                inner = inner.elts[0] if inner.elts else inner
            return TypeName.of(inner)
        if isinstance(node, ast.BinOp):
            return TypeName.of(node.left)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                return TypeName.of(ast.parse(node.value, mode="eval").body)
            except SyntaxError:
                return ""
        return ""


class Constructor:
    """Поле класса и объявленный тип того, что в него положили."""

    @staticmethod
    def attribute_types(node: ast.ClassDef) -> dict[str, str]:
        """`self._users = users` при `users: UserRepo` -> `{'_users': 'UserRepo'}`.

        Только прямое присваивание параметра полю. Вычисленные значения не
        разбираются: тип там неизвестен, а гадать в блокирующей проверке —
        значит завести ложные срабатывания.
        """
        for item in node.body:
            if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if item.name != "__init__":
                continue
            # Все три вида параметров: конструктор с `*` — обычная форма, а с
            # одними `args` роль хранилища в нём не видна вовсе.
            declared = {
                argument.arg: ast.unparse(argument.annotation)
                for argument in (
                    *item.args.posonlyargs, *item.args.args, *item.args.kwonlyargs
                )
                if argument.annotation is not None
            }
            found: dict[str, str] = {}
            for statement in item.body:
                if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                    continue
                target, value = statement.targets[0], statement.value
                if not (isinstance(target, ast.Attribute) and isinstance(value, ast.Name)):
                    continue
                owner = target.value
                if isinstance(owner, ast.Name) and owner.id == "self" and value.id in declared:
                    found[target.attr] = declared[value.id]
            return found
        return {}
