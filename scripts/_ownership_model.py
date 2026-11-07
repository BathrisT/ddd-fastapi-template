"""Модель происхождения и индекс явных определений проекта."""

import ast
from dataclasses import dataclass, field
from pathlib import Path

from _project import ROOT, source_root, tool_config


@dataclass(frozen=True, order=True)
class Origin:
    owner: str
    role: str


@dataclass(frozen=True)
class Value:
    origins: frozenset[Origin] = frozenset()
    refs: frozenset[str] = frozenset()
    item: "Value | None" = None
    fields: tuple[tuple[str, "Value"], ...] = ()

    def merge(self, other: "Value", cache: "dict[tuple[int, int], Value] | None" = None) -> "Value":
        if self is other:
            return self
        if not (self.origins or self.refs or self.item is not None or self.fields):
            return other
        if not (other.origins or other.refs or other.item is not None or other.fields):
            return self
        if cache is None:
            cache = {}
        pair = (id(self), id(other))
        if pair in cache:
            return cache[pair]
        item = self.item or other.item
        if self.item and other.item:
            item = self.item.merge(other.item, cache)
        fields = dict(self.fields)
        for name, value in other.fields:
            fields[name] = fields.get(name, EMPTY).merge(value, cache)
        result = Value(
            self.origins | other.origins,
            self.refs | other.refs,
            item,
            tuple(sorted(fields.items())),
        )
        cache[pair] = result
        return result

    def bind(self, bindings: dict[str, "Value"]) -> "Value":
        result = Value(
            refs=self.refs,
            item=self.item.bind(bindings) if self.item else None,
            fields=tuple((name, value.bind(bindings)) for name, value in self.fields),
        )
        for origin in self.origins:
            result = result.merge(bindings.get(origin.owner, Value(frozenset({origin}))))
        return result

    def sourced(self, origin: Origin) -> "Value":
        return Value(
            frozenset({origin}),
            self.refs,
            self.item.sourced(origin) if self.item else None,
            self.fields,
        )

    def replacing(
        self, old: "Value", new: "Value", cache: "dict[int, Value] | None" = None
    ) -> "Value":
        if self is old:
            return new
        if cache is None:
            cache = {}
        if id(self) in cache:
            return cache[id(self)]
        item = self.item.replacing(old, new, cache) if self.item else None
        fields = tuple((name, value.replacing(old, new, cache)) for name, value in self.fields)
        if item is self.item and all(
            a[1] is b[1] for a, b in zip(fields, self.fields, strict=True)
        ):
            result = self
        else:
            result = Value(self.origins, self.refs, item, fields)
        cache[id(self)] = result
        return result


EMPTY = Value()


@dataclass(frozen=True)
class Write:
    path: str
    line: int
    writer: Origin
    value: Value


@dataclass(frozen=True)
class Summary:
    result: Value = Value()
    writes: frozenset[Write] = frozenset()
    calls: frozenset[str] = frozenset()
    fields: tuple[tuple[str, Value], ...] = ()


@dataclass
class Function:
    key: str
    owner: Origin
    module: str
    path: str
    node: ast.FunctionDef | ast.AsyncFunctionDef

    def parameter(self, name: str) -> str:
        return f"{self.key}:{name}"


@dataclass
class Program:
    functions: dict[str, Function] = field(default_factory=dict)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)
    modules: dict[str, ast.Module] = field(default_factory=dict)
    imports: dict[str, dict[str, str]] = field(default_factory=dict)
    fields: dict[str, dict[str, Value]] = field(default_factory=dict)
    summaries: dict[str, Summary] = field(default_factory=dict)
    source: Path = field(default_factory=source_root)

    def role(self, path: Path) -> str:
        parts = path.relative_to(self.source).parts
        layout = tool_config("code_layout")
        usecases = Path(layout.get("use_cases_dir", "application/use_cases")).parts
        if parts[: len(usecases)] == usecases:
            return "usecase"
        return parts[0] if len(parts) > 1 else "module"

    def name(self, module: str, name: str) -> str:
        head, *tail = name.split(".")
        found = self.imports.get(module, {}).get(head, f"{module}.{head}")
        found = ".".join([found, *tail])
        seen: set[str] = set()
        while found not in seen:
            seen.add(found)
            parts = found.split(".")
            candidate = found
            for index in range(len(parts) - 1, 0, -1):
                aliases = self.imports.get(".".join(parts[:index]), {})
                if parts[index] in aliases:
                    candidate = ".".join([aliases[parts[index]], *parts[index + 1 :]])
                    break
            if candidate == found:
                break
            found = candidate
        return found

    def annotation(self, module: str, node: ast.expr | None) -> Value:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return Value()
        if node is None:
            return Value()
        if isinstance(node, ast.BinOp):
            return self.annotation(module, node.left).merge(self.annotation(module, node.right))
        if isinstance(node, ast.Subscript):
            args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            inner = Value()
            for arg in args:
                inner = inner.merge(self.annotation(module, arg))
            base = ast.unparse(node.value).split(".")[-1]
            if base in {"list", "dict", "tuple", "set", "Sequence", "Mapping"}:
                return Value(item=inner)
            return inner
        if isinstance(node, ast.Name | ast.Attribute):
            return Value(refs=frozenset({self.name(module, ast.unparse(node))}))
        return Value()

    def port(self, key: str) -> bool:
        node = self.classes.get(key)
        return ".ports." in key or bool(
            node and any(ast.unparse(base).split(".")[-1] == "Protocol" for base in node.bases)
        )

    def load(self) -> None:
        paths = sorted(self.source.rglob("*.py"))
        if not paths:
            raise ValueError(f"{self.source}: нет исходников для проверки")
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            if module.endswith(".__init__"):
                module = module[:-9]
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
            self.modules[module] = tree
            aliases: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    if node.level:
                        parent = (
                            module.split(".")
                            if path.name == "__init__.py"
                            else module.split(".")[:-1]
                        )
                        base = ".".join(
                            parent[: len(parent) - node.level + 1] + ([base] if base else [])
                        )
                    aliases.update(
                        {alias.asname or alias.name: f"{base}.{alias.name}" for alias in node.names}
                    )
                if isinstance(node, ast.Import):
                    aliases.update(
                        {
                            alias.asname or alias.name.split(".")[0]: alias.name
                            if alias.asname
                            else alias.name.split(".")[0]
                            for alias in node.names
                        }
                    )
            self.imports[module] = aliases
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    key = f"{module}.{node.name}"
                    self.classes[key] = node
                    self.fields[key] = {}
                    for method in node.body:
                        if isinstance(method, ast.Assign | ast.AnnAssign):
                            targets = (
                                method.targets
                                if isinstance(method, ast.Assign)
                                else [method.target]
                            )
                            for target in targets:
                                if isinstance(target, ast.Name):
                                    self.fields[key][target.id] = Value(
                                        frozenset({Origin(key, self.role(path))})
                                    )
                        if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                            method_key = f"{key}.{method.name}"
                            self.functions[method_key] = Function(
                                method_key, Origin(key, self.role(path)), module, relative, method
                            )
                elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    key = f"{module}.{node.name}"
                    self.functions[key] = Function(
                        key, Origin(module, self.role(path)), module, relative, node
                    )
        for function in self.functions.values():
            if function.node.name != "__init__":
                continue
            params = {
                arg.arg: self.annotation(function.module, arg.annotation)
                for arg in (
                    *function.node.args.posonlyargs,
                    *function.node.args.args,
                    *function.node.args.kwonlyargs,
                )
            }
            for node in ast.walk(function.node):
                if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        value = (
                            params.get(node.value.id, Value())
                            if isinstance(node.value, ast.Name)
                            else Value(frozenset({function.owner}))
                        )
                        if isinstance(node.value, ast.Name) and node.value.id in params:
                            value = Value(
                                frozenset(
                                    {
                                        Origin(
                                            f"{function.owner.owner}:field:{target.attr}",
                                            "parameter",
                                        )
                                    }
                                ),
                                value.refs,
                                value.item,
                            )
                        known = self.fields[function.owner.owner]
                        known[target.attr] = known.get(target.attr, Value()).merge(value)
        self.summaries = {key: Summary() for key in self.functions}
