"""Разбор вызовов и подстановка происхождения аргументов."""

import ast

from _ownership_model import Function, Origin, Value, Write

_ARGUMENT_PREFIX = "@argument:"


class Calls:
    def call(self, node: ast.Call) -> Value:
        target = self.expr(node.func)
        args = [self.expr(arg) for arg in node.args]
        kwargs = {kw.arg: self.expr(kw.value) for kw in node.keywords}
        for ref in target.refs:
            if ref.startswith("nested:"):
                return self.nested_call(ref.removeprefix("nested:"), args, kwargs)
        replacements = {"dataclasses.replace", "copy.replace"}
        if target.refs & replacements:
            self.write(args[0] if args else kwargs.get("obj", Value()), node)
            return self.fresh(refs=args[0].refs if args else frozenset())
        result = Value()
        resolved = False
        for ref in sorted(target.refs):
            if ref in self.program.classes:
                fields: tuple[tuple[str, Value], ...] = ()
                constructor = self.program.functions.get(f"{ref}.__init__")
                if constructor:
                    self.calls.add(constructor.key)
                    bindings = self.arguments(constructor, args, kwargs)
                    summary = self.program.summaries[constructor.key]
                    fields = tuple((name, value.bind(bindings)) for name, value in summary.fields)
                    declared = dict(fields)
                    declared.update(
                        {_ARGUMENT_PREFIX + name: value for name, value in bindings.items()}
                    )
                    fields = tuple(sorted(declared.items()))
                    self.writes.update(
                        Write(write.path, write.line, write.writer, write.value.bind(bindings))
                        for write in summary.writes
                    )
                result = result.merge(
                    Value(frozenset({self.function.owner}), frozenset({ref}), fields=fields)
                )
                continue
            function = self.program.functions.get(ref)
            owner = ref.rsplit(".", 1)[0]
            if self.program.port(owner):
                annotation = (
                    self.program.annotation(function.module, function.node.returns)
                    if function
                    else Value()
                )
                result = result.merge(annotation.sourced(Origin(owner, "infrastructure")))
                resolved = True
            elif function:
                self.calls.add(ref)
                summary = self.program.summaries[ref]
                bindings = self.arguments(function, args, kwargs)
                if isinstance(node.func, ast.Attribute):
                    receiver = self.expr(node.func.value)
                    for name, value in receiver.fields:
                        key = (
                            name.removeprefix(_ARGUMENT_PREFIX)
                            if name.startswith(_ARGUMENT_PREFIX)
                            else f"{owner}:field:{name}"
                        )
                        bindings[key] = value
                result = result.merge(summary.result.bind(bindings))
                self.writes.update(
                    Write(write.path, write.line, write.writer, write.value.bind(bindings))
                    for write in summary.writes
                )
                resolved = True
        if resolved or result.origins:
            return result
        if isinstance(node.func, ast.Name) and node.func.id in {
            "dict",
            "list",
            "tuple",
            "set",
            "str",
            "int",
            "float",
            "bool",
            "bytes",
        }:
            return self.fresh(item=(args[0].item if args else None))
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "get",
            "pop",
            "values",
            "items",
        }:
            parent = self.expr(node.func.value)
            if parent.item:
                return parent.item if node.func.attr in {"get", "pop"} else Value(item=parent.item)
        # Внешняя загрузка внутри инфраструктуры принадлежит этому адаптеру.
        if self.function.owner.role == "infrastructure":
            return self.fresh()
        return Value(frozenset({Origin("unknown", "unknown")}))

    @staticmethod
    def arguments(function: Function, args: list[Value], kwargs: dict) -> dict[str, Value]:
        params = [*function.node.args.posonlyargs, *function.node.args.args]
        if params and params[0].arg in {"self", "cls"}:
            params = params[1:]
        bindings = {
            function.parameter(param.arg): value for param, value in zip(params, args, strict=False)
        }
        bindings.update({function.parameter(name): value for name, value in kwargs.items() if name})
        for param in (*params, *function.node.args.kwonlyargs):
            bindings.setdefault(
                function.parameter(param.arg), Value(frozenset({Origin("unknown", "unknown")}))
            )
        return bindings

    def nested_call(self, name: str, args: list[Value], kwargs: dict) -> Value:
        if name in self.active:
            raise ValueError(
                f"{self.function.path}: рекурсивная вложенная функция {name} не поддерживается"
            )
        self.active.add(name)
        saved, result = self.env.copy(), self.result
        node = self.nested[name]
        for param, value in zip([*node.args.posonlyargs, *node.args.args], args, strict=False):
            self.env[param.arg] = value
        self.env.update({key: value for key, value in kwargs.items() if key})
        self.result = Value()
        self.block(node.body)
        value = self.result
        self.env, self.result = saved, result
        self.active.remove(name)
        return value
