"""Реализация называет свой порт вслух — и реализует его целиком.

Соответствия порту не проверяет сегодня никто: у `provide(SqlUserRepo,
provides=UserRepo)` обе стороны приняты как `Any`, и опечатка в имени метода
выходит наружу `AttributeError` в проде. Наследование отдаёт сверку сигнатур
типизатору; забытый метод не ловит и он — тот наследуется из порта телом `...`
и молча возвращает `None`, а объект создаёт контейнер по отражению.

Чужой класс правилу не подчиняется: наследование — табличка на классе, а не
режим порта, и реализация, которой нет в дереве, пропускается молча.

Правило — docs/rules/композиция-и-скоупы.md, пути — `[tool.port_inheritance]`.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural, require_dir, tool_config  # noqa: E402
from _providers import Binding, Bindings  # noqa: E402
from _symbols import Modules  # noqa: E402


class Classes:
    """Определения классов проекта по имени модуля: `app.x.y` → файл → класс."""

    @staticmethod
    def _base_names(node: ast.ClassDef) -> list[str]:
        return [
            name
            for base in node.bases
            for name in [getattr(base, "id", "") or getattr(base, "attr", "")]
            if name
        ]

    @staticmethod
    def ancestors(module: str, node: ast.ClassDef) -> set[str]:
        """Имена всех предков, включая непрямых: порт бывает под примесью."""
        found: set[str] = set()
        imports = Modules.imports(module)
        for name in Classes._base_names(node):
            if name in found:
                continue
            found.add(name)
            parent_module = imports.get(name, module)
            parent = Modules.definition(parent_module, name)
            if parent is not None:
                found |= Classes.ancestors(parent_module, parent)
        return found

    @staticmethod
    def methods(module: str, node: ast.ClassDef, ports_root: str = "") -> set[str]:
        """Публичные методы класса и его предков из дерева проекта.

        Тело, унаследованное от порта, — это `...`, поэтому `ports_root` отсекает
        всех предков-портов, а не только проверяемого: класс под двумя
        портами иначе засчитал бы метод одного в реализацию другого.
        """
        found = {
            inner.name
            for inner in node.body
            if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef)
            and not inner.name.startswith("_")
        }
        imports = Modules.imports(module)
        for name in Classes._base_names(node):
            parent_module = imports.get(name, module)
            if ports_root and parent_module.replace(".", "/").startswith(ports_root):
                continue
            parent = Modules.definition(parent_module, name)
            if parent is not None:
                found |= Classes.methods(parent_module, parent, ports_root)
        return found


class Ports:
    """Порт — класс из каталога портов. Место, а не имя: суффикса у них нет."""

    @staticmethod
    def declared(binding: Binding, ports_root: str) -> bool:
        if not (binding.port and binding.port_module and binding.impl_module):
            return False
        return binding.port_module.replace(".", "/").startswith(ports_root)

    @staticmethod
    def required(binding: Binding) -> set[str]:
        node = Modules.definition(binding.port_module, binding.port)
        if node is None:
            return set()
        return Classes.methods(binding.port_module, node)


class Adapters:
    """Кого композиция отдала под портом — и говорит ли он об этом вслух."""

    @staticmethod
    def _bindings(provider_dirs: list[str]) -> list[Binding]:
        found: list[Binding] = []
        for directory in provider_dirs:
            for path in sorted((ROOT / directory).rglob("*.py")):
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                except (SyntaxError, OSError):
                    continue
                found.extend(Bindings.of(tree, path.relative_to(ROOT).as_posix()))
        return found

    @staticmethod
    def violations(ports_root: str, provider_dirs: list[str], exempt: set[str]) -> list[str]:
        errors: list[str] = []
        for binding in Adapters._bindings(provider_dirs):
            if binding.impl in exempt or not Ports.declared(binding, ports_root):
                continue
            node = Modules.definition(binding.impl_module, binding.impl)
            if node is None:
                continue
            place = f"{binding.where}:{binding.line}"
            if binding.port not in Classes.ancestors(binding.impl_module, node):
                errors.append(
                    f"{place}: `{binding.impl}` отдаётся как `{binding.port}`, но порт свой не "
                    f"называет. Допиши `class {binding.impl}({binding.port})`: соответствие "
                    "порту не проверяет сегодня ни mypy, ни тесты — расхождение выйдет наружу "
                    "`AttributeError` в проде."
                )
                continue
            implemented = Classes.methods(binding.impl_module, node, ports_root)
            missing = Ports.required(binding) - implemented
            if missing:
                names = ", ".join(f"`{name}`" for name in sorted(missing))
                errors.append(
                    f"{place}: `{binding.impl}` наследует `{binding.port}`, но не реализует "
                    f"{names}. Метод наследуется из порта телом `...` — вызов не упадёт, "
                    "а молча вернёт `None`."
                )
        return errors


def main() -> int:
    config = tool_config("port_inheritance")
    if not config:
        print("Port inheritance: [tool.port_inheritance] не настроен — проверка пропущена.")
        return 0
    ports_root = str(config.get("ports_root", "")).strip("/")
    provider_dirs = config.get("provider_dirs", [])
    if not (ports_root and provider_dirs):
        return 0
    require_dir(ROOT / ports_root, "[tool.port_inheritance].ports_root")
    for directory in provider_dirs:
        require_dir(ROOT / directory, "[tool.port_inheritance].provider_dirs")

    errors = Adapters.violations(ports_root, provider_dirs, set(config.get("exempt", [])))
    if errors:
        for error in errors:
            print(error)
        print(f"\n{plural(len(errors), 'реализация', 'реализации', 'реализаций')} мимо порта.")
        print("Правило — docs/rules/композиция-и-скоупы.md")
        return 1
    print("Port inheritance: реализации названы портами и реализованы целиком. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
