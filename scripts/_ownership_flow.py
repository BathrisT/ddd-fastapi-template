"""Поток значений: присваивания, ветвления, вызовы и подстановка аргументов."""

import ast

from _ownership_calls import Calls
from _ownership_model import EMPTY, Function, Origin, Program, Summary, Value, Write


class Flow(Calls):
    def __init__(self, program: Program, function: Function) -> None:
        self.program = program
        self.function = function
        self.env: dict[str, Value] = {}
        self.result = Value()
        self.writes: set[Write] = set()
        self.calls: set[str] = set()
        self.fields: dict[str, Value] = {}
        self.nested: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.active: set[str] = set()
        args = function.node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            value = program.annotation(function.module, arg.annotation)
            origin = Origin(function.parameter(arg.arg), "parameter")
            self.env[arg.arg] = Value(frozenset({origin}), value.refs, value.item)
        self.env["self"] = Value(frozenset({function.owner}), frozenset({function.owner.owner}))
        self.env["cls"] = self.env["self"]

    def fresh(self, item: Value | None = None, refs: frozenset[str] = frozenset()) -> Value:
        return Value(frozenset({self.function.owner}), refs, item)

    def write(self, value: Value, node: ast.AST) -> None:
        self.writes.add(Write(self.function.path, node.lineno, self.function.owner, value))

    def expr(self, node: ast.AST | None) -> Value:
        if node is None:
            return Value()
        if isinstance(node, ast.Name):
            if node.id in self.env:
                return self.env[node.id]
            ref = self.program.name(self.function.module, node.id)
            # Модульное состояние не получает нового владельца при чтении.
            for statement in self.program.modules[self.function.module].body:
                if isinstance(statement, ast.Assign | ast.AnnAssign):
                    targets = (
                        statement.targets
                        if isinstance(statement, ast.Assign)
                        else [statement.target]
                    )
                    if any(
                        isinstance(target, ast.Name) and target.id == node.id for target in targets
                    ):
                        return Value(
                            frozenset({Origin(self.function.module, "module")}), frozenset({ref})
                        )
            return Value(refs=frozenset({ref}))
        if isinstance(node, ast.Await):
            return self.expr(node.value)
        if isinstance(node, ast.Attribute):
            parent = self.expr(node.value)
            if node.attr in dict(parent.fields):
                return dict(parent.fields)[node.attr]
            for ref in parent.refs:
                field = self.program.fields.get(ref, {}).get(node.attr)
                if field is not None:
                    value = (
                        field if field.origins else Value(parent.origins, field.refs, field.item)
                    )
                    self.attribute(parent, node.attr, value)
                    return value
            return Value(parent.origins, frozenset(f"{ref}.{node.attr}" for ref in parent.refs))
        if isinstance(node, ast.Subscript):
            parent = self.expr(node.value)
            self.expr(node.slice)
            key = self.item_key(node.slice)
            return dict(parent.fields).get(key, parent.item or parent)
        if isinstance(node, ast.Call):
            return self.call(node)
        if isinstance(node, ast.IfExp):
            self.expr(node.test)
            return self.expr(node.body).merge(self.expr(node.orelse))
        if isinstance(node, ast.NamedExpr):
            value = self.expr(node.value)
            self.assign(node.target, value)
            return value
        if isinstance(node, ast.Dict):
            item = Value()
            fields: dict[str, Value] = {}
            for key, value in zip(node.keys, node.values, strict=True):
                self.expr(key)
                entry = self.expr(value)
                item = item.merge(entry.item if key is None and entry.item else entry)
                if key is not None and self.item_key(key):
                    fields[self.item_key(key)] = entry
            return Value(
                frozenset({self.function.owner}), item=item, fields=tuple(sorted(fields.items()))
            )
        if isinstance(node, ast.List | ast.Set | ast.Tuple):
            item = Value()
            fields = {}
            for index, entry in enumerate(node.elts):
                value = self.expr(entry)
                item = item.merge(value)
                fields[f"item:{index!r}"] = value
            return Value(
                frozenset({self.function.owner}), item=item, fields=tuple(sorted(fields.items()))
            )
        if isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
            for generator in node.generators:
                iterable = self.expr(generator.iter)
                self.assign(generator.target, iterable.item or iterable)
                for condition in generator.ifs:
                    self.expr(condition)
            item = self.expr(node.value if isinstance(node, ast.DictComp) else node.elt)
            return self.fresh(item)
        if isinstance(node, ast.Constant):
            return self.fresh()
        value = Value()
        for child in ast.iter_child_nodes(node):
            value = value.merge(self.expr(child))
        return value

    def assign(self, target: ast.AST, value: Value) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = value
        elif isinstance(target, ast.Tuple | ast.List):
            for entry in target.elts:
                self.assign(entry, value.item or value)
        elif isinstance(target, ast.Starred):
            self.assign(target.value, value)
        elif isinstance(target, ast.Attribute | ast.Subscript):
            if not (
                self.function.node.name == "__init__"
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                self.write(self.expr(target.value), target)
            if isinstance(target, ast.Attribute):
                parent = self.expr(target.value)
                own = parent is self.env.get("self")
                self.attribute(parent, target.attr, value)
                if own:
                    self.fields[target.attr] = value
            else:
                old = self.expr(target.value)
                fields = dict(old.fields)
                key = self.item_key(target.slice)
                if key:
                    fields[key] = value
                else:
                    fields = {name: entry.merge(value) for name, entry in fields.items()}
                updated = Value(
                    old.origins,
                    old.refs,
                    (old.item or Value()).merge(value),
                    tuple(sorted(fields.items())),
                )
                self.replace(old, updated)

    def replace(self, old: Value, updated: Value) -> None:
        shared: dict[int, Value] = {}
        self.env = {name: value.replacing(old, updated, shared) for name, value in self.env.items()}
        self.fields = {
            name: value.replacing(old, updated, shared) for name, value in self.fields.items()
        }

    def attribute(self, parent: Value, name: str, value: Value) -> None:
        fields = dict(parent.fields)
        fields[name] = value
        self.replace(
            parent, Value(parent.origins, parent.refs, parent.item, tuple(sorted(fields.items())))
        )

    @staticmethod
    def item_key(node: ast.AST) -> str:
        return f"item:{node.value!r}" if isinstance(node, ast.Constant) else ""

    def block(self, statements: list[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self.nested[node.name] = node
                self.env[node.name] = Value(refs=frozenset({f"nested:{node.name}"}))
                continue
            if isinstance(node, ast.ClassDef):
                continue
            if isinstance(node, ast.Assign | ast.AnnAssign):
                value = self.expr(node.value)
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    self.assign(target, value)
            elif isinstance(node, ast.AugAssign):
                self.expr(node.value)
                self.assign(node.target, self.expr(node.target))
            elif isinstance(node, ast.Return):
                self.result = self.result.merge(self.expr(node.value))
            elif isinstance(node, ast.If):
                self.expr(node.test)
                self.branches([node.body, node.orelse])
            elif isinstance(node, ast.For | ast.AsyncFor | ast.While):
                self.loop(node)
            elif isinstance(node, ast.With | ast.AsyncWith):
                for item in node.items:
                    value = self.expr(item.context_expr)
                    if item.optional_vars:
                        self.assign(item.optional_vars, value)
                self.block(node.body)
            elif isinstance(node, ast.Try | ast.TryStar):
                self.branches([node.body, *[handler.body for handler in node.handlers]])
                self.block(node.orelse)
                self.block(node.finalbody)
            elif isinstance(node, ast.Match):
                self.expr(node.subject)
                bodies = [case.body for case in node.cases]
                if not any(
                    case.guard is None and self.irrefutable(case.pattern) for case in node.cases
                ):
                    bodies.append([])
                self.branches(bodies)
            else:
                self.expr(node)

    @staticmethod
    def join(
        left: dict[str, Value],
        right: dict[str, Value],
        shared: dict[tuple[int, int], Value] | None = None,
    ) -> dict[str, Value]:
        if shared is None:
            shared = {}
        result: dict[str, Value] = {}
        for name in left.keys() | right.keys():
            first, second = left.get(name, EMPTY), right.get(name, EMPTY)
            result[name] = first.merge(second, shared)
        return result

    def branches(self, bodies: list[list[ast.stmt]]) -> None:
        before = self.env.copy()
        before_fields = self.fields.copy()
        merged: dict[str, Value] = {}
        fields: dict[str, Value] = {}
        for body in bodies:
            self.env = before.copy()
            self.fields = before_fields.copy()
            self.block(body)
            shared: dict[tuple[int, int], Value] = {}
            merged = self.join(merged, self.env, shared)
            fields = self.join(fields, self.fields, shared)
        self.env = merged
        self.fields = fields

    @staticmethod
    def irrefutable(pattern: ast.pattern) -> bool:
        if isinstance(pattern, ast.MatchAs):
            return pattern.pattern is None or Flow.irrefutable(pattern.pattern)
        return isinstance(pattern, ast.MatchOr) and any(
            Flow.irrefutable(p) for p in pattern.patterns
        )

    def loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        iterable = self.expr(node.iter) if isinstance(node, ast.For | ast.AsyncFor) else None
        before, before_fields = self.env.copy(), self.fields.copy()
        for _ in range(max(8, len(list(ast.walk(node))) * 2)):
            previous, previous_fields = self.env.copy(), self.fields.copy()
            if iterable is None:
                self.expr(node.test)
            else:
                self.assign(node.target, iterable.item or iterable)
            self.block(node.body)
            shared: dict[tuple[int, int], Value] = {}
            self.env = self.join(before, self.env, shared)
            self.fields = self.join(before_fields, self.fields, shared)
            if self.env == previous and self.fields == previous_fields:
                break
        else:
            raise ValueError(f"{self.function.path}:{node.lineno}: анализ цикла не сошёлся")
        self.block(node.orelse)

    def run(self) -> Summary:
        self.block(self.function.node.body)
        return Summary(
            self.result,
            frozenset(self.writes),
            frozenset(self.calls),
            tuple(sorted(self.fields.items())),
        )
