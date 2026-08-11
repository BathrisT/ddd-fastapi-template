"""Чтение из репозитория внутри цикла: N+1 запросов.

    poetry run python scripts/check_n_plus_one.py

N+1 — это ФОРМА, а не приговор, и сторож устроен вокруг этого. Цикл из четырёх
чтений по вложениям блока стоит четыре миллисекунды, и пакетный метод их не
окупит. Больше того, «починка» умеет делать хуже: `IN` на десять тысяч
идентификаторов планировщик разберёт медленнее десяти тысяч точечных чтений по
индексу, а замена цикла на `JOIN` по нескольким коллекциям даёт декартово
произведение — один запрос вместо двухсот, но сто тысяч строк вместо двухсот.

Поэтому сторож не выносит вердикт о важности — он сообщает КЛАСС РОСТА. Рост
статически виден, важность нет: как часто зовут этот код, сколько там строк на
самом деле и ждёт ли ответа живой человек, знает только автор.

Два признака, каждый объясняется одной фразой:

- **безграничный** — цикл идёт по результату чтения из репозитория, значит
  число итераций растёт вместе с данными. Цикл по атрибуту одной загруженной
  сущности (`block.file_ids`) ограничен по смыслу и признака не получает;
- **вложенный** — чтение лежит внутри двух и более циклов, то есть запросов
  выходит произведение, а не сумма.

Отсюда два тира:

- **оба признака — отказ.** Рост квадратичный, и это не вопрос вкуса.
- **только безграничность — предупреждение.** Линейный рост бывает уместен:
  редкая административная операция, путь без живого пользователя. Решает
  автор, но видеть это он обязан.
- **остальное — молчание.** Список, в котором лежит заведомо безобидное,
  перестают читать целиком, и тогда пропадают и первые два тира.

Чего сторож принципиально не видит: частоту вызова. Красный тир означает
«стоимость растёт квадратично», а не «болит прямо сейчас» — редкий импорт
плана попадёт в красный тир так же, как ручка дашборда.
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
    # Слово в ТИПЕ зависимости (`users: UserRepo` в конструкторе) или в имени
    # поля (`self._user_repo`). Тип — основной признак: поле сплошь и рядом
    # называют по домену (`self._users`), и роль хранилища в его имени не
    # видна вовсе.
    #
    # Сравнение ПОСЛОВНОЕ, а не по подстроке, и это не придирка: `store` как
    # подстрока сидит внутри `restore`, `dal` — внутри `modal`, и
    # `self._restore_service.get_state(...)` в цикле стал бы ложным N+1.
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

        Одного имени мало, и это выяснилось на собственном шаблоне: там
        единственный репозиторий всюду зовётся `self._users` — по домену, во
        множественном числе, — и ни одного слова-маркера в имени нет. Сторож
        честно молчал, а выглядело это как «N+1 не найдено».

        Тип же объявлен прямо в конструкторе: `def __init__(self, users:
        UserRepo, ...)`. Роль хранилища живёт в имени ТИПА и не зависит от
        того, как автор назвал поле, — поэтому основной признак теперь этот, а
        имя осталось запасным для полей, заведённых мимо конструктора.
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

        Только прямое присваивание параметра полю — та самая форма, которой
        собирают зависимости в этом коде. Вычисленные значения (`self._x =
        make(y)`) намеренно не разбираются: тип там неизвестен, а гадать —
        значит завести ложные срабатывания в блокирующей проверке.
        """
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name != "__init__":
                continue
            # Все три вида параметров, а не только позиционные: конструктор
            # `def __init__(self, *, users_repo: UserRepo)` — обычная форма, и
            # с одними `args` роль хранилища у него не видна вовсе, то есть
            # квадратичное чтение в цикле не считается. Сосед по правилу
            # (`check_db_access`) собирает все три.
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
    """Обход с тремя состояниями: типы полей класса, стек циклов, происхождение имён.

    Происхождение сбрасывается на каждой функции: одноимённая переменная в
    соседнем методе к нашей отношения не имеет, а без сброса её связывание
    протекло бы сюда и объявило бы ограниченный цикл безграничным.

    Стек циклов сбрасывается там же и по той же причине. Функция, объявленная
    внутри цикла, выполняется тогда, когда её позовут, а не там, где написана,
    — унаследованный стек означал бы «мы внутри цикла» там, где никакого цикла
    в момент исполнения нет.
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
        # вычисляется один раз, вне тела, и своим же циклом не окружено.
        # Иначе `for item in await repo.get_by_order(order.id)` внутри другого
        # цикла классифицировался бы как вложенный сам в себя — обычный
        # линейный N+1 получал красный тир.
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
