"""Чтение из репозитория внутри цикла: N+1 запросов.

N+1 — это ФОРМА, а не приговор. Цикл из четырёх чтений стоит четыре
миллисекунды, а «починка» умеет делать хуже: `IN` на десять тысяч
идентификаторов планировщик разберёт медленнее точечных чтений по индексу.
Поэтому сторож сообщает КЛАСС РОСТА, а не важность: рост виден статически,
важность — нет.

Признака два: БЕЗГРАНИЧНЫЙ (цикл по результату чтения, число итераций растёт
с данными) и ВЛОЖЕННЫЙ (чтение внутри двух циклов, запросов выходит
произведение). Оба — отказ, рост квадратичный. Только безграничность —
предупреждение, решает автор. Остальное — молчание, иначе список перестают
читать целиком и пропадают первые два тира.

Чего сторож не видит: частоту вызова.
"""

import ast
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import source_root, tool_config  # noqa: E402

APP_DIR = source_root()

# `self._user_repo` -> {'self', 'user', 'repo'}; `UserRepo` -> {'user', 'repo'}.
# Второе обязательно: имена типов пишут заглавными по CamelCase, и разбор
# только по разделителям отдал бы одно слово `userrepo`, в котором маркера нет.
_SEPARATORS = re.compile(r"[._\s]+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+")


def words(name: str) -> set[str]:
    """Имя, разобранное на слова: по разделителям и по границам CamelCase."""
    found: set[str] = set()
    for token in _SEPARATORS.split(name):
        found.update(part.lower() for part in _CAMEL.findall(token))
    return found


_DEFAULTS: dict[str, list[str]] = {
    # Слово в ТИПЕ зависимости или в имени поля; тип основной, потому что поле
    # сплошь и рядом зовут по домену (`self._users`). Сравнение ПОСЛОВНОЕ:
    # `store` сидит внутри `restore`, `dal` — внутри `modal`.
    "repository_markers": [
        "repo",
        "repos",
        "repository",
        "repositories",
        "dao",  # Data Access Object
        "dal",  # Data Access Layer
        "gateway",  # гексагональная архитектура
        "store",
        "storage",
        "db",
        "query",  # читающая сторона CQRS
        "queries",
        "finder",
    ],
    # Префиксы читающих методов. Запись в цикле (`save`, `delete`) — норма:
    # обновляют N сущностей, других вариантов нет.
    "read_prefixes": ["get", "list", "find", "count", "exists", "fetch", "load"],
    # Осознанные исключения — путями, а не пометкой по месту. Строка в конфиге
    # видна в ревью и требует объяснения; инлайновый `# noqa` ставят молча, и
    # через полгода никто не помнит, разбирались там или отмахнулись.
    "exclude": [],
}


def setting(key: str) -> list[str]:
    value = tool_config("query_loops").get(key, _DEFAULTS[key])
    return [str(item) for item in value]


class Call:
    @staticmethod
    def receiver(call: ast.Call) -> str:
        return ast.unparse(call.func.value) if isinstance(call.func, ast.Attribute) else ""

    @staticmethod
    def method(call: ast.Call) -> str:
        return call.func.attr if isinstance(call.func, ast.Attribute) else ""

    @staticmethod
    def self_attribute(call: ast.Call) -> str:
        """`self._users.get(...)` -> `_users`; иначе пусто."""
        func = call.func
        if not isinstance(func, ast.Attribute):
            return ""
        receiver = func.value
        if isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Name):
            return receiver.attr if receiver.value.id == "self" else ""
        return ""

    @staticmethod
    def is_repo_read(call: ast.Call, attribute_types: dict[str, str]) -> bool:
        """Чтение из хранилища — по имени поля ИЛИ по типу из конструктора.

        Одного имени мало: в самом шаблоне репозиторий зовётся `self._users`,
        без слова-маркера, и сторож молчал, а выглядело это как «N+1 не
        найдено». Роль живёт в имени ТИПА, имя поля осталось запасным.
        """
        if not Call.method(call).startswith(tuple(setting("read_prefixes"))):
            return False
        markers = set(setting("repository_markers"))
        if words(Call.receiver(call)) & markers:
            return True
        declared = attribute_types.get(Call.self_attribute(call), "")
        return bool(declared) and bool(words(declared) & markers)

    @staticmethod
    def awaited(node: ast.expr | None) -> ast.Call | None:
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            return node.value
        return None


def names_in(node: ast.expr) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


class Constructor:
    @staticmethod
    def attribute_types(node: ast.ClassDef) -> dict[str, str]:
        """`self._users = users` при `users: UserRepo` -> `{'_users': 'UserRepo'}`.

        Только прямое присваивание параметра полю. Вычисленные значения не
        разбираются: тип там неизвестен, а гадать в блокирующей проверке —
        значит завести ложные срабатывания.
        """
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name != "__init__":
                continue
            # Все три вида параметров: конструктор с `*` — обычная форма, а с
            # одними `args` роль хранилища в нём не видна вовсе. Сосед по
            # правилу (`check_db_access`) собирает так же.
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


class Finder(ast.NodeVisitor):
    """Три состояния: типы полей класса, стек циклов, происхождение имён.

    И происхождение, и стек сбрасываются на каждой функции: одноимённая
    переменная соседнего метода объявила бы ограниченный цикл безграничным, а
    функция внутри цикла исполняется не там, где написана.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.loops: list[tuple[ast.For | ast.AsyncFor, bool]] = []
        self.unbounded: dict[str, bool] = {}
        self.attribute_types: dict[str, str] = {}
        self.red: list[str] = []
        self.yellow: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Карта «поле -> объявленный тип» из конструктора, до обхода методов."""
        saved = self.attribute_types
        self.attribute_types = {**saved, **Constructor.attribute_types(node)}
        self.generic_visit(node)
        self.attribute_types = saved

    def _enter_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        saved_names, saved_loops = self.unbounded, self.loops
        self.unbounded, self.loops = {}, []
        self.generic_visit(node)
        self.unbounded, self.loops = saved_names, saved_loops

    visit_FunctionDef = _enter_function
    visit_AsyncFunctionDef = _enter_function

    def visit_Assign(self, node: ast.Assign) -> None:
        call = Call.awaited(node.value)
        from_repo = call is not None and Call.is_repo_read(call, self.attribute_types)
        for target in node.targets:
            for name in names_in(target):
                self.unbounded[name] = from_repo
        self.generic_visit(node)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        # Выражение цикла обходится ДО того, как цикл попадёт в стек: оно
        # вычисляется один раз и своим же циклом не окружено. Иначе линейный
        # N+1 классифицировался бы как вложенный сам в себя.
        self.visit(node.iter)

        direct = Call.awaited(node.iter)
        # Безграничность наследуется только от ГОЛОГО имени: `for x in items`.
        # `for fid in block.file_ids` — атрибут одной загруженной сущности, и
        # он ограничен по смыслу, даже если сам `block` пришёл из репозитория.
        # Это ровно тот пример, который докстринг объявляет освобождённым.
        inherited = isinstance(node.iter, ast.Name) and self.unbounded.get(node.iter.id, False)
        from_repo = (
            direct is not None and Call.is_repo_read(direct, self.attribute_types)
        ) or bool(inherited)
        for name in names_in(node.target):
            self.unbounded[name] = from_repo

        self.loops.append((node, from_repo))
        for field, value in ast.iter_fields(node):
            if field == "iter":
                continue
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, ast.AST):
                    self.visit(item)
        self.loops.pop()

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop

    def visit_Await(self, node: ast.Await) -> None:
        call = Call.awaited(node)
        if call is not None and self.loops and Call.is_repo_read(call, self.attribute_types):
            self._classify(call)
        self.generic_visit(node)

    def _classify(self, call: ast.Call) -> None:
        loop_names: set[str] = set()
        for loop, _ in self.loops:
            loop_names |= names_in(loop.target)
        arguments = [*call.args, *[keyword.value for keyword in call.keywords]]
        # Аргумент из переменной цикла — то, что отличает N+1 от одного чтения,
        # случайно оказавшегося внутри цикла с постоянными аргументами.
        if not any(names_in(argument) & loop_names for argument in arguments):
            return

        growing = sum(1 for _, unbounded_source in self.loops if unbounded_source)
        if growing == 0:
            return
        # Квадратичный рост — это ДВА растущих уровня, а не просто глубина два.
        # `for attempt in range(3): for user in await repo.list(): ...` вложен,
        # но внешний цикл — константа, и произведение остаётся линейным.
        nested = growing >= 2

        where = (
            f"{self.path.relative_to(APP_DIR.parent).as_posix()}:{call.lineno}: "
            f"{Call.receiver(call)}.{Call.method(call)}(...) в цикле"
        )
        if nested:
            self.red.append(f"{where}, вложенном в другой — запросов произведение")
        else:
            self.yellow.append(f"{where} по результату чтения — запросов по числу строк")


def main() -> int:
    red: list[str] = []
    yellow: list[str] = []
    files = sorted(APP_DIR.rglob("*.py"))
    if not files:
        print(f"ОШИБКА: в {APP_DIR} нет ни одного .py — проверка смотрит в пустоту.")
        return 2
    excluded = setting("exclude")
    for path in files:
        relative = path.relative_to(APP_DIR.parent).as_posix()
        if any(fnmatch(relative, pattern) for pattern in excluded):
            continue
        finder = Finder(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            # Про синтаксис и кодировку ругается ruff; сторожу тут сказать
            # нечего, а трейсбек без имени файла — худшее из сообщений.
            continue
        finder.visit(tree)
        red.extend(finder.red)
        yellow.extend(finder.yellow)

    if yellow:
        print(f"ПРЕДУПРЕЖДЕНИЕ: линейный рост запросов, {len(yellow)} мест:")
        for item in yellow:
            print(f"  {item}")
        print(
            "  Уместно, если операция редкая или коллекция мала; иначе заведи "
            "пакетное чтение в репозитории.\n"
        )

    if red:
        print(f"ОТКАЗ: квадратичный рост запросов, {len(red)} мест:")
        for item in red:
            print(f"  {item}")
        print(
            "\nЧтение из репозитория внутри вложенного цикла по безграничному "
            "источнику: число запросов — произведение размеров коллекций. "
            "Забери данные одним пакетным чтением ДО циклов и разложи по "
            "словарю. Осознанное исключение — в [tool.query_loops].exclude."
        )
        return 1

    print("N+1: чтений из репозитория с квадратичным ростом нет. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
