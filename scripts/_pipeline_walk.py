"""Обход тела метода в дерево узлов карты пайплайна.

Что становится узлом — решено не здесь, а в правилах (docs/rules/карта-пайплайна.md).
Коротко, потому что код ниже читается только вместе с этим:

1. Узел — пересечение границы объекта: вызов через то, что пришло в `__init__`.
   Приватный метод того же класса узлом не становится — обход проваливается
   сквозь него, а имя метода уходит в подпись узлов, которые он породил.
2. Статический помощник (`TextHygiene.nfc`) не в счёт: объекту его не давали.
3. `if` — узел, если возвращает или делает вызовы. С телом — родительский узел.
4. Финальный возврат не показывается, условный — показывается: показываем то,
   чего нельзя предсказать по структуре.
5. Параллельность определяется синтаксисом: `asyncio.gather`, `TaskGroup`,
   `create_task`. Аргументы `gather` именуются, иначе группировка теряется.
"""

import ast

from _pipeline import Given, Signature, Source

PARALLEL = {"gather", "create_task", "ensure_future"}
SELF = "self"


class Step:
    """Узел карты. Словарь, а не класс с полями: он же формат JSON."""

    @staticmethod
    def make(title: str, kind: str, **rest: object) -> dict:
        node: dict = {"title": title, "kind": kind}
        node.update({key: value for key, value in rest.items() if value})
        return node


class Walker:
    """Строит дерево вызовов от метода вглубь, не выходя за исходники проекта."""

    def __init__(self, source: Source, max_depth: int) -> None:
        self._source = source
        self._max_depth = max_depth

    def body(self, statements: list[ast.stmt], context: dict, depth: int) -> list[dict]:
        """Узлы по списку операторов в порядке исходника."""
        nodes: list[dict] = []
        last = statements[-1] if statements else None
        for statement in statements:
            nodes.extend(self._statement(statement, context, depth, final=statement is last))
        return nodes

    def _statement(self, statement: ast.stmt, context: dict, depth: int, *, final: bool) -> list[dict]:
        if isinstance(statement, ast.If):
            return self._branch(statement, context, depth)
        if isinstance(statement, ast.Try):
            return self._try(statement, context, depth)
        if isinstance(statement, ast.For | ast.AsyncFor | ast.While):
            return self._loop(statement, context, depth)
        if isinstance(statement, ast.With | ast.AsyncWith):
            return self._with(statement, context, depth)
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            return self._nested(statement, context, depth)
        if isinstance(statement, ast.Return | ast.Raise):
            # Правило 4: окончание функции предсказуемо по структуре, ранний
            # выход — нет, и его рисует ветка. Узла здесь не будет, а вызовы
            # ВНУТРИ возврата остаются работой: молчание о них даёт вход без
            # единого шага, неотличимый от пустого.
            _ = final
            return self._calls(statement, context, depth)
        return self._calls(statement, context, depth)

    def _nested(self, node: ast.FunctionDef | ast.AsyncFunctionDef, context: dict, depth: int) -> list[dict]:
        """Функция, объявленная внутри тела, — обычно и есть вся работа.

        Так пишут обратный вызов: команда CLI отдаёт `go(search: SearchUseCase)`
        тому, кто соберёт зависимости и позовёт. Пропусти её — и вход окажется
        пустым, хотя весь сценарий лежит на два отступа правее.
        """
        inner = dict(context, given={**context["given"], **Given.of(node, self._source)})
        children = self.body(node.body, inner, depth)
        if not children:
            return []
        return [
            Step.make(
                f"{node.name}()", "call", children=children, signature=Signature.of(node), line=node.lineno
            )
        ]

    def _branch(self, statement: ast.If, context: dict, depth: int) -> list[dict]:
        """`if` и каждый `elif`/`else` — отдельная ветка, если она что-то делает."""
        nodes: list[dict] = []
        condition = f"if {self._text(statement.test)}"
        nodes.extend(self._arm(statement.body, condition, context, depth))
        rest = statement.orelse
        if len(rest) == 1 and isinstance(rest[0], ast.If):
            nodes.extend(self._branch(rest[0], context, depth))
        elif rest:
            nodes.extend(self._arm(rest, "else", context, depth))
        return nodes

    def _arm(self, body: list[ast.stmt], condition: str, context: dict, depth: int) -> list[dict]:
        """Одна ветка: слева условие, дальше — что она делает.

        Условие стоит первым: читают сверху вниз. Выход работу не поглощает —
        `return self._answer(...)` и решение, и вызов, поэтому под ним висит
        то, что этот вызов делает.
        """
        exit_statement = next((s for s in body if isinstance(s, ast.Return | ast.Raise)), None)
        line = body[0].lineno if body else 0
        rest = [s for s in body if s is not exit_statement]
        children = self.body(rest, context, depth)
        leaving = self.body([exit_statement], context, depth) if exit_statement is not None else []
        if not children and not leaving:
            if exit_statement is None:
                return []
            return [
                Step.make(
                    condition, "branch", note=self._text(exit_statement), file=context["module"], line=line
                )
            ]
        if exit_statement is not None:
            children.append(
                Step.make(
                    self._text(exit_statement),
                    "exit",
                    children=leaving,
                    file=context["module"],
                    line=exit_statement.lineno,
                )
            )
        else:
            children.extend(leaving)
        return [Step.make(condition, "branch", children=children, file=context["module"], line=line)]

    def _try(self, statement: ast.Try, context: dict, depth: int) -> list[dict]:
        """Тело `try` прозрачно, а каждый `except`, который что-то делает, — ветка."""
        nodes = self.body(statement.body, context, depth)
        for handler in statement.handlers:
            caught = self._text(handler.type) if handler.type else "Exception"
            nodes.extend(self._arm(handler.body, f"except {caught}", context, depth))
        nodes.extend(self.body(statement.finalbody, context, depth))
        return nodes

    def _loop(self, statement: ast.For | ast.AsyncFor | ast.While, context: dict, depth: int) -> list[dict]:
        """Цикл — родительский узел: тот же вызов внутри повторяется, и это видно.

        Заголовок обходится отдельно и ПЕРЕД телом: `async for chunk in
        flow.execute(...)` делает главный вызов именно там. Пропусти его — и
        сценарий, ради которого цикл написан, исчезнет из карты.
        """
        header = statement.test if isinstance(statement, ast.While) else statement.iter
        nodes = self._calls(header, context, depth)
        children = self.body(statement.body, context, depth)
        if not children:
            return nodes
        if isinstance(statement, ast.While):
            title = f"while {self._text(statement.test)}"
        else:
            title = f"for {self._text(statement.target)} in {self._text(statement.iter)}"
        return [*nodes, Step.make(title, "loop", children=children, file=context["module"], line=statement.lineno)]

    def _with(self, statement: ast.With | ast.AsyncWith, context: dict, depth: int) -> list[dict]:
        """`async with` вокруг тела прозрачен, кроме `TaskGroup` — там параллель.

        Сам открывающий вызов при этом работа, и немалая: `async with
        AutonomousSession.open() as session` — это отдельная транзакция.
        """
        group = False
        nodes: list[dict] = []
        for item in statement.items:
            if isinstance(item.context_expr, ast.Call) and "TaskGroup" in self._text(item.context_expr.func):
                group = True
                continue
            nodes.extend(self._calls(item.context_expr, context, depth))
        children = self.body(statement.body, context, depth)
        if not children:
            return nodes
        if group:
            return [*nodes, Step.make("asyncio.TaskGroup", "parallel", children=children)]
        return [*nodes, *children]

    def _calls(self, statement: ast.AST | None, context: dict, depth: int) -> list[dict]:
        """Вызовы внутри оператора или выражения — в порядке появления в исходнике."""
        if statement is None:
            return []
        calls = sorted(
            (node for node in ast.walk(statement) if isinstance(node, ast.Call)),
            key=lambda node: (node.lineno, node.col_offset),
        )
        nodes: list[dict] = []
        consumed: set[int] = set()
        for call in calls:
            if id(call) in consumed:
                continue
            produced = self._call(call, context, depth, consumed)
            nodes.extend(produced)
        return nodes

    def _call(self, call: ast.Call, context: dict, depth: int, consumed: set[int]) -> list[dict]:
        name = self._text(call.func)
        if name.split(".")[-1] in PARALLEL and name.startswith(("asyncio.", "loop.")):
            return self._parallel(call, context, depth, consumed)
        if isinstance(call.func, ast.Name):
            return self._helper(call, context, depth, consumed)
        if not isinstance(call.func, ast.Attribute):
            return []
        receiver, attribute = call.func.value, call.func.attr
        if isinstance(receiver, ast.Name) and receiver.id == SELF:
            return self._own(attribute, call, context, depth, consumed)
        holder = self._holder(receiver, context)
        if not holder:
            return []
        return [self._crossing(holder, attribute, call, context, depth)]

    def _helper(self, call: ast.Call, context: dict, depth: int, consumed: set[int]) -> list[dict]:
        """`record(recorder, request, ...)` — не узел, но и не пустота.

        Функция уровня модуля границы объекта не пересекает: её никто никому
        не давал. Обход проваливается сквозь неё, как сквозь приватный метод,
        и приписывает её имя сделанному; типы берём из её аннотаций.
        """
        name = getattr(call.func, "id", "")
        found = self._source.function(name, context["module"]) if name else None
        if found is None or depth >= self._max_depth:
            return []
        node, module = found
        key = (module, name)
        if key in context["stack"]:
            return [Step.make(f"{name}()", "repeat", file=module, line=node.lineno)]
        for inner in ast.walk(call):
            if isinstance(inner, ast.Call):
                consumed.add(id(inner))
        inner_context = {
            "owner": context["owner"],
            "module": module,
            "given": Given.of(node, self._source),
            "stack": context["stack"] | {key},
            "through": name,
        }
        produced = self.body(node.body, inner_context, depth)
        for made in produced:
            made.setdefault("note", name)
        return produced

    def _own(self, attribute: str, call: ast.Call, context: dict, depth: int, consumed: set[int]) -> list[dict]:
        """`self._x.m()` — пересечение границы; `self._m()` — свой же метод, прозрачен."""
        if attribute in context["owner"].fields:
            return []
        method = self._source.method(context["owner"], attribute)
        if method is None or depth >= self._max_depth:
            return []
        key = (context["owner"].name, attribute)
        if key in context["stack"]:
            return [Step.make(f"{context['owner'].name}.{attribute}", "repeat")]
        inner = dict(context, stack=context["stack"] | {key}, through=attribute)
        for node in ast.walk(call):
            if isinstance(node, ast.Call):
                consumed.add(id(node))
        produced = self.body(method.body, inner, depth)
        for node in produced:
            node.setdefault("note", attribute)
        return produced

    def _holder(self, receiver: ast.expr, context: dict) -> str:
        """Тип того, через кого идёт вызов, — только если объекту его ДАЛИ."""
        if isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Name):
            if receiver.value.id == SELF:
                return context["owner"].fields.get(receiver.attr, "")
            return ""
        if isinstance(receiver, ast.Name):
            return context["given"].get(receiver.id, "")
        return ""

    def _crossing(self, holder: str, attribute: str, call: ast.Call, context: dict, depth: int) -> dict:
        """Узел на границе объекта: порт, за ним реализация, за ней её тело."""
        target = self._source.target(holder)
        owner = self._source.owner(target.impl)
        node = Step.make(
            f"{target.impl}.{attribute}",
            "call",
            port=target.port,
            note=context.get("through", ""),
            file=target.impl_module,
            line=call.lineno,
        )
        method = self._source.method(owner, attribute) if owner else None
        if method is None:
            node["kind"] = "leaf"
            return node
        node["file"] = owner.module if owner else ""
        node["line"] = method.lineno
        node["signature"] = Signature.of(method)
        node["doc"] = self._source.doc(owner.module) if owner else ""
        key = (target.impl, attribute)
        if key in context["stack"]:
            node["kind"] = "repeat"
            return node
        if depth + 1 >= self._max_depth:
            return node
        assert owner is not None
        inner = {"owner": owner, "module": owner.module, "given": {}, "stack": context["stack"] | {key}}
        children = self.body(method.body, inner, depth + 1)
        if children:
            node["children"] = children
        return node

    def _parallel(self, call: ast.Call, context: dict, depth: int, consumed: set[int]) -> list[dict]:
        """`asyncio.gather(...)`: каждый аргумент — своя ветка, и она ИМЕНУЕТСЯ.

        Без имени ветки группировка теряется, и последовательные вызовы внутри
        одной половины выглядят параллельными соседней.
        """
        branches: list[dict] = []
        for argument in call.args:
            if not isinstance(argument, ast.Call):
                continue
            for node in ast.walk(argument):
                if isinstance(node, ast.Call):
                    consumed.add(id(node))
            title = self._text(argument.func).replace("self.", "")
            children = self._call(argument, context, depth, set())
            if len(children) == 1 and children[0].get("kind") != "branch":
                child = dict(children[0])
                child["note"] = title
                branches.append(child)
                continue
            branches.append(Step.make(title, "task", children=children) if children else Step.make(title, "leaf"))
        consumed.add(id(call))
        if not branches:
            return []
        return [Step.make("asyncio.gather", "parallel", children=branches)]

    @staticmethod
    def _text(node: ast.AST | None) -> str:
        """Текст из кода, а не пересказ: подписи узлов не сочиняются."""
        if node is None:
            return ""
        try:
            return ast.unparse(node)
        except Exception:  # pragma: no cover — нечитаемый узел не должен ронять карту
            return "?"
