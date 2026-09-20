"""Карта пайплайна: от точки входа до последнего вызова, в JSON.

Зачем. Пройти сценарий по коду мешает ровно одно место — порт: IDE ведёт к
`Protocol`, у которого тела нет, и цепочка рвётся. Кто стоит за портом, знает
композиция; карта её читает и сшивает цепочку обратно.

Чего скрипт НЕ делает: не сочиняет подписи. Всё, что видно в узле, — либо имя
из кода, либо текст ветки из кода, либо адрес файла. Докстроки в подписи не
идут: они пишутся для человека, читающего файл, и в одну строку не ложатся.

Что считать входом, скрипт не угадывает: каталоги входов объявлены в
`[tool.pipeline_map].entries`. HTTP, очередь, CLI, бот — разница только в том,
как вход подписан, а не в том, как его найти.

Результат — `.pipeline/map.json` (поле `schema` — версия формата, по ней
плагин отличает несовместимый файл от устаревшего).
"""

import ast
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline import Given, Owner, Signature, Source  # noqa: E402
from _pipeline_walk import Walker  # noqa: E402
from _project import ROOT, plural, require_dir, source_root, tool_config  # noqa: E402
from _providers import TypeNames  # noqa: E402

SCHEMA = 1
DEFAULT_OUT = ".pipeline/map.json"
DEFAULT_DEPTH = 12
HTTP = {"get", "post", "put", "patch", "delete", "head", "options"}


class Entry:
    """Точка входа: модульная функция в объявленном каталоге входов."""

    @staticmethod
    def title(node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> str:
        """Маршрут подписывается декоратором, остальное — именем функции.

        В слое входа классов нет, имя функции там служебное (`api_search`), а
        снаружи вход известен как `GET /api/search`. Где декоратор адреса не
        даёт, имя функции — единственное, что есть, и его же видит отправитель.
        """
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr
            first = decorator.args[0] if decorator.args else None
            path = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else ""
            if method in HTTP:
                # Пустой путь у вложенного роутера законен: адрес договаривает
                # префикс, которого в этом файле нет. Придумывать `/` нельзя —
                # такого адреса не существует; остаётся имя функции.
                return f"{method.upper()} {path or node.name}"
            if path:
                return path
        return f"{node.name}()" if kind != "http" else node.name

class Entries:
    """Поиск входов по объявленным каталогам."""

    @staticmethod
    def declared() -> list[dict]:
        configured = tool_config("pipeline_map").get("entries", [])
        return [dict(item) for item in configured]

    @staticmethod
    def _callbacks(tree: ast.Module) -> dict[str, str]:
        """Функция, отданная ЗНАЧЕНИЕМ, и имя, под которым её позовут.

        `add_search_commands(sub)` входом не является: она только объявляет
        подкоманду и кладёт обработчик в `func=`. Вход — этот обработчик, и
        зовут его `search`, а не `_search`: переданное значением зовут не отсюда.
        """
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
        labels: dict[str, str] = {}
        declared: dict[str, str] = {}

        def look(node: ast.AST) -> None:
            # Обход в порядке ИСХОДНИКА, а не вширь: `ast.walk` идёт по
            # уровням, и тогда все `add_parser` файла успевают перезаписать
            # друг друга раньше, чем встретится первый `set_defaults`, —
            # все команды файла получают одно и то же имя.
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                named = call.args[0] if call.args else None
                if (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "add_parser"
                    and isinstance(named, ast.Constant)
                    and isinstance(named.value, str)
                ):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            declared[target.id] = named.value
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if isinstance(keyword.value, ast.Name) and keyword.value.id in names:
                        holder = getattr(getattr(node.func, "value", None), "id", "")
                        labels[keyword.value.id] = declared.get(holder, keyword.value.id)
            for child in ast.iter_child_nodes(node):
                look(child)

        look(tree)
        return labels

    @staticmethod
    def of(source: Source, declaration: dict) -> list[dict]:
        directory = str(declaration.get("dir", ""))
        kind = str(declaration.get("kind", "entry"))
        require_dir(ROOT / directory, "[tool.pipeline_map].entries")
        found: list[dict] = []
        for path, module in sorted(source.modules.items()):
            if not path.startswith(f"{directory}/") or path.endswith("__init__.py"):
                continue
            callbacks = Entries._callbacks(module.tree)
            for node in module.tree.body:
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if node.name in callbacks:
                    title = callbacks[node.name]
                elif callbacks or node.name.startswith("_"):
                    continue  # регистратор рядом с обработчиком либо приватный помощник
                else:
                    title = Entry.title(node, kind)
                found.append(
                    {
                        "title": title,
                        "kind": kind,
                        "function": node.name,
                        "file": path,
                        "line": node.lineno,
                        "signature": Signature.of(node),
                        "doc": module.doc,
                        "node": node,
                    }
                )
        return found


class Map:
    """Сборка карты целиком: входы, обход, запись файла."""

    @staticmethod
    def build() -> dict:
        settings = tool_config("pipeline_map")
        depth = int(settings.get("max_depth", DEFAULT_DEPTH))
        source = Source(source_root(), ROOT)
        walker = Walker(source, depth)
        entries: list[dict] = []
        for declaration in Entries.declared():
            for entry in Entries.of(source, declaration):
                node = entry.pop("node")
                owner = Owner("", entry["file"], None, {})
                context = {"owner": owner, "module": entry["file"], "given": Given.of(node, source), "stack": set()}
                entry["children"] = walker.body(node.body, context, 0)
                entries.append(entry)
        return {
            "schema": SCHEMA,
            "generated": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "root": source_root().relative_to(ROOT).as_posix(),
            # Абсолютный корень нужен смотрелке: PyCharm открывает файл по
            # полному пути (`/api/file/<путь>:<строка>` на порту 63342), а в
            # узлах путь относительный — он же уезжает в плагин, которому
            # абсолютный путь дал бы чужую машину.
            "root_abs": ROOT.as_posix(),
            # Имя проекта, каким его знает IDE: второй способ прыжка
            # (`jetbrains://pycharm/navigate/reference`) без него не работает, а
            # угадать его нельзя — корень репозитория и корень проекта IDE
            # совпадают не всегда. Ключ `[tool.pipeline_map].ide_project`.
            "project": Map.ide_project(),
            # Путь ВНУТРИ проекта IDE: у встроенного сервера пути считаются от
            # корня проекта, а корень сторожей — `backend`, не репозиторий.
            "root_in_project": Map.root_in_project(),
            "wiring": {port: impl for port, (impl, _) in sorted(source.bindings.items())},
            "entries": entries,
        }

    @staticmethod
    def ide_token() -> str:
        """Токен встроенного сервера IDE: без него каждый прыжок в код — диалог.

        Страницу с диска IDE считает чужим источником и спрашивает НА КАЖДЫЙ
        клик. Уходит ТОЛЬКО в `map.html` внутри `.pipeline/` — каталога из
        `.gitignore`; в `map.json` его нет. Выключается `ide_token = false`.
        """
        if tool_config("pipeline_map").get("ide_token") is False:
            return ""
        roots = [Path(os.environ["APPDATA"]) / "JetBrains"] if os.environ.get("APPDATA") else []
        roots.append(Path.home() / ".config" / "JetBrains")
        found = [
            path
            for root in roots
            if root.is_dir()
            for path in root.glob("*/user.web.token")
        ]
        if not found:
            return ""
        newest = max(found, key=lambda path: path.stat().st_mtime)
        return newest.read_text(encoding="utf-8").strip()

    @staticmethod
    def _project_root() -> Path:
        """Корень проекта IDE — ближайший предок с `.git`, иначе корень сторожей."""
        for candidate in [ROOT, *ROOT.parents]:
            if (candidate / ".git").exists():
                return candidate
        return ROOT

    @staticmethod
    def ide_project() -> str:
        configured = str(tool_config("pipeline_map").get("ide_project", "") or "")
        return configured or Map._project_root().name

    @staticmethod
    def root_in_project() -> str:
        inside = ROOT.relative_to(Map._project_root()).as_posix()
        return "" if inside == "." else inside

    @staticmethod
    def count(nodes: list[dict]) -> int:
        return sum(1 + Map.count(node.get("children", [])) for node in nodes)

    @staticmethod
    def main() -> int:
        if not Entries.declared():
            print(
                "ОШИБКА НАСТРОЙКИ: `[tool.pipeline_map].entries` пуст.\n"
                "  Карта строится от точек входа, а какие каталоги ими считать —\n"
                "  знает проект, а не скрипт. Пример:\n"
                '  entries = [{ dir = "app/interface/api/routes", kind = "http" }]'
            )
            return 2
        built = Map.build()
        out = ROOT / str(tool_config("pipeline_map").get("out", DEFAULT_OUT))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(built, ensure_ascii=False, indent=2), encoding="utf-8")
        page = Path(__file__).resolve().parent / "pipeline_view.html"
        viewer = out.with_suffix(".html")
        token = Map.ide_token()
        viewer.write_text(
            page.read_text(encoding="utf-8")
            .replace("/*MAP*/", json.dumps(built, ensure_ascii=False))
            .replace("/*TOKEN*/", json.dumps(token)),
            encoding="utf-8",
        )
        steps = sum(Map.count(entry["children"]) for entry in built["entries"])
        print(
            f"Карта пайплайна: {plural(len(built['entries']), 'вход', 'входа', 'входов')}, "
            f"{plural(steps, 'шаг', 'шага', 'шагов')}, "
            f"{plural(len(built['wiring']), 'порт', 'порта', 'портов')} сшито.\n"
            f"  {out.relative_to(ROOT).as_posix()}\n"
            f"  {viewer.relative_to(ROOT).as_posix()} — открыть в браузере, смотреть глазами"
            + ("" if token else "\n  Токен IDE не найден: прыжок в код будет спрашивать подтверждение на клик.")
        )
        empty = [entry["title"] for entry in built["entries"] if not entry["children"]]
        if empty:
            print(f"  Без единого шага: {', '.join(empty)} — вход ничего не зовёт или зовёт мимо своих зависимостей.")
        return 0


if __name__ == "__main__":
    raise SystemExit(Map.main())
