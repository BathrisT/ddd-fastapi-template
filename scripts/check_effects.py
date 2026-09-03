"""Порядок и тождество в пути записи: фиксация, публикация, постановка задачи.

Единственные проверки про ПОРЯДОК операций, а не про форму, — и заведены
потому, что ошибки этого класса не ловят тесты ПО КОНСТРУКЦИИ: предписанный
`NoopEventPublisher` задач не создаёт, так что нарушение «публиковать после
фиксации» в тесте физически не проявляется. Событие с нулевым идентификатором
тоже не падает — оно уходит в никуда.

1. `publish` не раньше `commit`: воркер разбирает очередь мгновенно и своей
   сессией, читая базу раньше, чем в ней появится строка.
2. После сохранения не читают `id` у переданного объекта — его присваивает
   база. Ловится пара «результат `save` выброшен» + «читают `x.id`».

Третьего правила («аргументы задачи только именованные») здесь нет намеренно:
его держит сигнатура порта, и дублирующая проверка не сработала бы ни разу.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import source_root, tool_config  # noqa: E402

APP_DIR = source_root()

_DEFAULTS = {
    "commit_methods": ["commit"],
    "publish_methods": ["publish"],
    "must_use_result": ["save"],
    "identity_attr": ["id"],
}


def setting(key: str) -> list[str]:
    value = tool_config("effects").get(key, _DEFAULTS[key])
    return [str(item) for item in value]


class Calls:
    """Разбор вызовов внутри одного тела функции."""

    @staticmethod
    def unwrap(node: ast.expr) -> ast.Call | None:
        """`await f()` и `f()` — один и тот же вызов для наших целей."""
        if isinstance(node, ast.Await):
            node = node.value
        return node if isinstance(node, ast.Call) else None

    @staticmethod
    def method_name(call: ast.Call) -> str:
        """`self._events.publish(...)` -> `publish`; голая функция -> пусто."""
        return call.func.attr if isinstance(call.func, ast.Attribute) else ""

    @staticmethod
    def receiver(call: ast.Call) -> str:
        """Текст получателя вызова для внятного сообщения об ошибке."""
        if isinstance(call.func, ast.Attribute):
            return ast.unparse(call.func.value)
        return ast.unparse(call.func)

    @staticmethod
    def in_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
        """Вызовы тела функции, кроме тех, что лежат во вложенных функциях."""
        return [node for node in Scope.nodes(func) if isinstance(node, ast.Call)]


class Scope:
    @staticmethod
    def nodes(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
        """Узлы тела функции, БЕЗ тел вложенных функций и лямбд.

        Строки вложенной выполнятся, когда её позовут, а не там, где написаны.
        Своим обходом, а не `ast.walk`: с ним `commit()` из замыкания
        засчитывался как «коммит выше по тексту».
        """
        collected: list[ast.AST] = []
        stack: list[ast.AST] = list(ast.iter_child_nodes(func))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            collected.append(node)
            stack.extend(ast.iter_child_nodes(node))
        return collected


class Blocks:
    """Кто кого объемлет: коммит в ветке не годится для кода вне ветки.

    Коммит с `return` внутри `if` стоит выше публикации, но на пути к ней его
    не было. Засчитываем, только если блок коммита ОБЪЕМЛЕТ блок публикации.
    """

    @staticmethod
    def chain(
        func: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[int, list[tuple[ast.stmt, str]]]:
        """Для каждого узла — цепочка объемлющих блоков, снизу вверх.

        Блок — это ПАРА «инструкция и её поле»: без поля ветки `if` и `else`
        неразличимы, и коммит в одной прикрывал бы публикацию в другой.
        """
        chains: dict[int, list[tuple[ast.stmt, str]]] = {}
        stack: list[tuple[ast.AST, list[tuple[ast.stmt, str]]]] = [(func, [])]
        while stack:
            node, enclosing = stack.pop()
            chains[id(node)] = enclosing
            for field, value in ast.iter_fields(node):
                children = value if isinstance(value, list) else [value]
                deeper = (
                    [*enclosing, (node, field)]
                    if isinstance(node, ast.stmt) and node is not func
                    else enclosing
                )
                for child in children:
                    if not isinstance(child, ast.AST):
                        continue
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        continue
                    stack.append((child, deeper))
        return chains

    @staticmethod
    def path(
        chains: dict[int, list[tuple[ast.stmt, str]]], node: ast.AST
    ) -> list[tuple[ast.stmt, str]]:
        """Блок, в котором узел исполняется, — без его собственной инструкции.

        Последний элемент цепочки — та инструкция, внутри которой узел лежит.
        Оставь её — и два вызова в одном блоке никогда не совпадут.
        """
        return chains.get(id(node), [])[:-1]

    @staticmethod
    def encloses(
        chains: dict[int, list[tuple[ast.stmt, str]]], outer: ast.AST, inner: ast.AST
    ) -> bool:
        """Лежит ли `outer` в блоке, объемлющем `inner` (или в том же блоке)."""
        outer_path = Blocks.path(chains, outer)
        inner_path = Blocks.path(chains, inner)
        return outer_path == inner_path[: len(outer_path)]


class Reads:
    @staticmethod
    def of_attribute(
        func: ast.FunctionDef | ast.AsyncFunctionDef, name: str, attr: str
    ) -> list[int]:
        """Строки, где читают `<name>.<attr>` внутри функции."""
        return [
            node.lineno
            for node in Scope.nodes(func)
            if isinstance(node, ast.Attribute)
            and node.attr == attr
            and isinstance(node.value, ast.Name)
            and node.value.id == name
        ]


class Bindings:
    @staticmethod
    def last_before(
        func: ast.FunctionDef | ast.AsyncFunctionDef, name: str, lineno: int
    ) -> ast.expr | None:
        """Чем связано имя непосредственно перед указанной строкой.

        Отличает свежесозданную сущность от загруженной: у второй `id`
        настоящий. Цикл `for x in ...` тоже связывание — без него чтение
        `x.id` приписалось бы присваиванию из предыдущего цикла.
        """
        best: tuple[int, ast.expr] | None = None
        for node in Scope.nodes(func):
            if isinstance(node, ast.Assign):
                targets: list[ast.expr] = list(node.targets)
                value: ast.expr = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                targets, value = [node.target], node.iter
            elif isinstance(node, ast.NamedExpr):
                targets, value = [node.target], node.value
            else:
                continue
            if node.lineno >= lineno:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id == name:
                    if best is None or node.lineno > best[0]:
                        best = (node.lineno, value)
        return best[1] if best else None

    @staticmethod
    def is_construction(value: ast.expr | None) -> bool:
        """Заведён ли объект прямо здесь: `Entity(...)` или `Entity.factory(...)`.

        Только это значит «идентификатора ещё нет»: `plan.next_block()` отдаёт
        сохранённую сущность из агрегата. Признак — заглавная буква у корня
        вызова, то есть соглашение; в проекте с другим стилем его выключают.
        """
        if not isinstance(value, ast.Call):
            return False
        root = value.func
        while isinstance(root, ast.Attribute):
            root = root.value
        return isinstance(root, ast.Name) and root.id[:1].isupper()


class Functions:
    @staticmethod
    def walk(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]


def _sources() -> list[tuple[Path, ast.AST]]:
    parsed: list[tuple[Path, ast.AST]] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        try:
            parsed.append((path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
        except (SyntaxError, UnicodeDecodeError):
            continue
    if not parsed:
        print(f"ОШИБКА: в {APP_DIR} нет ни одного .py — проверка смотрит в пустоту.")
        raise SystemExit(2)
    return parsed


def check_publish_after_commit(sources: list[tuple[Path, ast.AST]]) -> list[str]:
    """Публикация раньше фиксации в пределах одной функции."""
    commit_names = setting("commit_methods")
    publish_names = setting("publish_methods")
    errors: list[str] = []

    for path, tree in sources:
        for func in Functions.walk(tree):
            commits: list[ast.Call] = []
            publishes: list[ast.Call] = []
            for call in Calls.in_function(func):
                name = Calls.method_name(call)
                if name in commit_names:
                    commits.append(call)
                elif name in publish_names:
                    publishes.append(call)
            if not commits or not publishes:
                continue
            chains = Blocks.chain(func)
            for publish in publishes:
                # Коммит засчитывается, только если он стоит выше по тексту И
                # его блок объемлет блок публикации. Одного номера строки мало:
                # коммит в ветке с ранним `return` текстуально выше, а на пути
                # к публикации за этой веткой его не было.
                covered = any(
                    commit.lineno < publish.lineno and Blocks.encloses(chains, commit, publish)
                    for commit in commits
                )
                if covered:
                    continue
                errors.append(
                    f"{path.relative_to(APP_DIR.parent).as_posix()}:{publish.lineno}: "
                    f"`{Calls.receiver(publish)}.publish(...)` в `{func.name}` "
                    "не прикрыт коммитом: выше по этому пути исполнения "
                    "`commit()` нет. Воркер разберёт задачу своей сессией и не "
                    "найдёт незафиксированной строки."
                )
    return errors


def check_stale_after_save(sources: list[tuple[Path, ast.AST]]) -> list[str]:
    """Результат сохранения выброшен, а `id` читают у переданного объекта.

    Именно пара: само по себе выбрасывание законно и встречается чаще ошибки.
    Ловим случай, где нулевой идентификатор поедет дальше.
    """
    names = setting("must_use_result")
    identity = setting("identity_attr")[0]
    errors: list[str] = []

    for path, tree in sources:
        for func in Functions.walk(tree):
            for node in Scope.nodes(func):
                if not isinstance(node, ast.Expr):
                    continue
                call = Calls.unwrap(node.value)
                if call is None or Calls.method_name(call) not in names:
                    continue
                if not call.args or not isinstance(call.args[0], ast.Name):
                    continue
                passed = call.args[0].id
                bound = Bindings.last_before(func, passed, node.lineno)
                if not Bindings.is_construction(bound):
                    # Загружено, а не заведено здесь: идентификатор настоящий,
                    # читать его после сохранения законно.
                    continue
                for line in Reads.of_attribute(func, passed, identity):
                    if line > node.lineno and Bindings.last_before(func, passed, line) is bound:
                        errors.append(
                            f"{path.relative_to(APP_DIR.parent).as_posix()}:{line}: "
                            f"`{passed}.{identity}` читается после того, как "
                            f"результат `{Calls.method_name(call)}(...)` выброшен "
                            f"(строка {node.lineno}). Идентификатор присваивает "
                            f"база — у `{passed}` он остался нулевым."
                        )
                        break
    return errors


def main() -> int:
    sources = _sources()
    errors = check_publish_after_commit(sources) + check_stale_after_save(sources)
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} нарушений порядка операций.")
        print("Публиковать после фиксации; работать с возвращённым объектом.")
        return 1
    print("Effects: публикация после фиксации, результат сохранения в ходу. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
