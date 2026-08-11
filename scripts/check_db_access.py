"""Guard the database boundary: сессию открывает композиция, SQL пишут репозитории.

Два правила, оба про одно — кто имеет право разговаривать с базой напрямую.

**Сессию создаёт тот, кто отвечает за её жизнь.** У сессии есть владелец:
контейнер открывает её на вход (`RequestProvider.session`) и закрывает вместе
с ним, а короткие записи, которые обязаны пережить транзакцию сценария, идут
через единственный фасад автономной работы. Сервис, открывающий сессию сам,
эту границу стирает: он решает за сценарий, что и когда зафиксировано, а
`commit()` посреди чужой транзакции фиксирует ещё и то, что сценарий
фиксировать не собирался. Так это и было в `B24ConversationRepair` — две
сессии на один вызов, обе руками.

**SQL живёт в репозиториях.** Правило 4 CLAUDE.md («репозитории возвращают
доменные модели») описывает выход, но не вход: пока `sa.update(...)` можно
написать где угодно, запрос к таблице появляется в сервисе канала, в сценарии,
в хендлере — и рядом с ним поселяются ORM-модели, а вместе с ними знание о
колонках там, где должно быть знание о предметной области.

Оба списка — в `[tool.db_access]` pyproject.toml, потому что путей-исключений
у каждого проекта свои: где-то фасад автономной записи один, где-то их два.
Инлайновой пометки-побега нет намеренно, как и в соседних сторожах: завести
исключение можно только правкой конфига, и она видна в ревью.

Проверяется только `app/` — миграции и тесты работают с базой по определению.
"""

import ast
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, is_repository_port, names_repository, source_root  # noqa: E402

APP_DIR = source_root()
PYPROJECT = ROOT / "pyproject.toml"


def _config() -> dict:
    raw = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return raw.get("tool", {}).get("db_access", {})


def _allowed(path: Path, prefixes: list[str]) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return any(relative == p or relative.startswith(f"{p}/") for p in prefixes)


def _sources() -> list[tuple[Path, str, ast.Module]]:
    files: list[tuple[Path, str, ast.Module]] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        files.append((path, path.relative_to(ROOT).as_posix(), tree))
    return files


def _aliases(tree: ast.Module, names: set[str]) -> set[str]:
    """Имена вместе с псевдонимами импорта.

    `from sqlalchemy.ext.asyncio import AsyncSession as S` обходил бы сверку по
    имени — ровно эта дыра закрыта в соседних сторожах.
    """
    local = set(names)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            local.update(a.asname for a in node.names if a.name in names and a.asname)
    return local


def check_session_owners(config: dict) -> list[str]:
    """Сессию открывают только те, кто за неё отвечает."""
    markers = set(config.get("session_markers", []))
    owners = config.get("session_owners", [])
    if not markers:
        return []

    errors: list[str] = []
    for path, relative, tree in _sources():
        if _allowed(path, owners):
            continue
        local = _aliases(tree, markers)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in local:
                errors.append(
                    f"{relative}:{node.lineno}: `{name}(...)` — сессия создаётся вне композиции. "
                    "Сессию на вход открывает контейнер, автономную запись — её единственный "
                    "фасад; иначе объект сам решает, что и когда зафиксировано."
                )
    return errors


def check_orm_stays_in_repositories(config: dict) -> list[str]:
    """SQLAlchemy — только там, где ей положено быть."""
    packages = set(config.get("orm_packages", []))
    allowed = config.get("orm_allowed", [])
    if not packages:
        return []

    errors: list[str] = []
    for path, relative, tree in _sources():
        if _allowed(path, allowed):
            continue
        for node in ast.walk(tree):
            imported = ""
            if isinstance(node, ast.Import):
                imported = next(
                    (a.name for a in node.names if a.name.split(".")[0] in packages), ""
                )
            elif isinstance(node, ast.ImportFrom):
                imported = node.module or ""
                if imported.split(".")[0] not in packages:
                    imported = ""
            if imported:
                errors.append(
                    f"{relative}:{node.lineno}: импорт `{imported}` вне слоя доступа к данным. "
                    "Запрос к таблице пишет репозиторий и возвращает доменную модель — "
                    "иначе знание о колонках расползается по сервисам и сценариям."
                )
    return errors


def _module_to_path(module: str) -> str:
    return module.replace(".", "/")


def _imported_from(tree: ast.Module, package: str) -> set[str]:
    """Имена, ввезённые из пакета (с учётом псевдонимов)."""
    prefix = package.replace("/", ".")
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(prefix):
            names.update(a.asname or a.name for a in node.names)
    return names


def _import_sources(tree: ast.Module) -> dict[str, str]:
    """`{имя: модуль, откуда ввезено}` — чтобы узнать, где живёт порт."""
    sources: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                sources[alias.asname or alias.name] = node.module
    return sources


class DataAccess:
    """Классы, которые знают про колонки, — то есть адаптеры доступа к данным.

    Признак — использование ORM-модели, а НЕ импорт sqlalchemy, и разница тут
    решающая. `SqlWelcomeJournal` писал в таблицу, не импортируя sqlalchemy
    вовсе: ему хватало `AutonomousSession` и ORM-модели. Проверка по импорту
    пропустила бы ровно тот класс, ради которого правило и заводится, — и при
    этом отказала бы моделям, примесям, `Base`, движку, сессии и committer'у,
    которые sqlalchemy импортируют законно.
    """

    @staticmethod
    def classes(models_package: str) -> dict[str, str]:
        """`{имя класса: файл}` по всему `app/`, кроме самого пакета моделей."""
        found: dict[str, str] = {}
        for path, relative, tree in _sources():
            if relative.startswith(f"{models_package}/"):
                continue
            models = _imported_from(tree, models_package)
            if not models:
                continue
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                used = any(
                    isinstance(inner, ast.Name) and inner.id in models
                    for inner in ast.walk(node)
                )
                if used:
                    found[node.name] = relative
        return found


class Bindings:
    """Где порт встречается со своей реализацией.

    В Python связь структурная: `SqlUserRepo` не наследует `UserRepo`, и по
    самим файлам их не сопоставить. Но композиция обязана назвать обе стороны
    явно — иначе контейнер не соберёт граф, — поэтому пара берётся оттуда.
    """

    @staticmethod
    def of(tree: ast.Module, relative: str, adapters: dict) -> list[tuple]:
        """`[(адаптер, порт, модуль порта, файл, строка)]`."""
        sources = _import_sources(tree)
        pairs: list[tuple] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "provide":
                adapter = getattr(node.args[0], "id", "") if node.args else ""
                if adapter not in adapters:
                    continue
                port = next(
                    (getattr(k.value, "id", "") for k in node.keywords if k.arg == "provides"),
                    "",
                )
                pairs.append((adapter, port, sources.get(port, ""), relative, node.lineno))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                built = {
                    getattr(inner.func, "id", "")
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.Call)
                }
                adapter = next((name for name in built if name in adapters), "")
                if not adapter or not node.returns:
                    continue
                port = getattr(node.returns, "id", "")
                pairs.append((adapter, port, sources.get(port, ""), relative, node.lineno))
        return pairs


def check_repository_ports(config: dict) -> list[str]:
    """Адаптер знает про колонки — его порт обязан называться репозиторием.

    Оба соседних сторожа опознают репозиторий ПО ИМЕНИ ТИПА: `check_composition`
    отбивает `FromDishka[*Repo]` во входе, `check_n_plus_one` считает чтения в
    цикле. Оба смотрят на имя ПОРТА, потому что именно порт объявлен в
    сигнатуре. Значит порт, названный `WelcomeJournal`, `UserStore` или
    `ClientGateway`, делает обоих слепыми — вход берёт репозиторий мимо
    сценария, чтение в цикле не считается, и ни одна проверка не срабатывает.

    `Repo` в имени — это паттерн, то есть намерение («набор хранимых
    сущностей»), а не транспорт: транспорт назвал бы порт `SqlUserRepo`, и вот
    это как раз запрещено.
    """
    models_package = str(config.get("orm_models", "")).strip("/")
    ports_package = str(config.get("repository_ports", "")).strip("/")
    provider_dirs = config.get("provider_dirs", [])
    if not (models_package and ports_package and provider_dirs):
        return []

    adapters = DataAccess.classes(models_package)
    if not adapters:
        return []

    errors: list[str] = []
    for path, relative, tree in _sources():
        if not _allowed(path, provider_dirs):
            continue
        for adapter, port, module, where, line in Bindings.of(tree, relative, adapters):
            if not port:
                errors.append(
                    f"{where}:{line}: `{adapter}` работает с ORM-моделями и отдаётся как есть, "
                    "без порта. Сценарий обязан видеть порт, а не адаптер."
                )
                continue
            if not names_repository(port):
                errors.append(
                    f"{where}:{line}: `{adapter}` работает с ORM-моделями, а его порт назван "
                    f"`{port}`. Репозиторий, не названный репозиторием, невидим для "
                    "`check_composition` и `check_n_plus_one` — оба опознают его по имени типа "
                    "в сигнатуре. В имени должно стоять `Repo` или `Repository`, за которыми "
                    "идёт заглавная буква или конец имени."
                )
            elif is_repository_port(port) and not _module_to_path(module).startswith(
                f"{ports_package}/"
            ):
                errors.append(
                    f"{where}:{line}: порт `{port}` лежит в `{_module_to_path(module)}`, "
                    f"а порты репозиториев живут в `{ports_package}/`."
                )
    return errors


class RepositoryNames:
    """Имя переменной с типом-репозиторием само говорит, что это репозиторий.

    На месте вызова видно ИМЯ, а не тип: `for user in users: await
    self._users.get(user.id)` читается как работа со списком, хотя это N+1
    запросов. То же и для сторожа стоимости — `check_n_plus_one` опознаёт
    репозиторий по типу из конструктора, и там, где тип не виден (результат
    фабрики, локальный псевдоним, нетипизированный параметр), у него остаётся
    только имя.

    Правило не выдумано: в боевом проекте на 533 параметра-репозитория имя без
    `repo` носили четыре. Соглашение уже существует — здесь оно просто
    перестаёт быть устным.

    Поля проверяются наравне с параметрами, хотя аннотации у них нет: тип поля
    виден только через конструктор, а `self._plans.get(...)` в цикле читают
    глазами и без конструктора. Тот же боевой проект как раз тут и расходится —
    параметры у него `plans_repo`, а поля `self._plans`, — и расходится в
    сторону, где роль хранилища на месте вызова не видна.
    """

    WORDS = frozenset({"repo", "repos", "repository", "repositories"})

    @staticmethod
    def says(name: str) -> bool:
        return bool(RepositoryNames.WORDS & set(name.lower().strip("_").split("_")))

    @staticmethod
    def _typed_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
        found = {}
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            if argument.annotation and names_repository(ast.unparse(argument.annotation)):
                found[argument.arg] = ast.unparse(argument.annotation)
        return found

    @staticmethod
    def violations(relative: str, tree: ast.Module) -> list[str]:
        errors: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            typed = RepositoryNames._typed_args(node)
            for name, annotation in typed.items():
                if not RepositoryNames.says(name):
                    errors.append(
                        f"{relative}:{node.lineno}: параметр `{name}: {annotation}` — "
                        "роль хранилища видна только в типе. Добавь `repo` в имя."
                    )
            # Куда параметр лёг: на месте вызова читают именно поле.
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Assign) or not isinstance(inner.value, ast.Name):
                    continue
                source = typed.get(inner.value.id)
                target = inner.targets[0]
                if not source or not isinstance(target, ast.Attribute):
                    continue
                if not RepositoryNames.says(target.attr):
                    errors.append(
                        f"{relative}:{inner.lineno}: поле `self.{target.attr}` держит "
                        f"`{source}` — на месте вызова роль хранилища не видна. "
                        "Добавь `repo` в имя."
                    )
        return errors


def check_repository_variable_names(config: dict) -> list[str]:
    """Переменная с типом-репозиторием называет себя репозиторием."""
    skip = config.get("naming_exempt", [])
    errors: list[str] = []
    for path, relative, tree in _sources():
        if _allowed(path, skip):
            continue
        errors.extend(RepositoryNames.violations(relative, tree))
    return errors


def main() -> int:
    config = _config()
    errors = (
        check_session_owners(config)
        + check_orm_stays_in_repositories(config)
        + check_repository_ports(config)
        + check_repository_variable_names(config)
    )
    if errors:
        print("Доступ к базе мимо границы:\n")
        for error in errors:
            print(f"  {error}")
        print(f"\nВсего: {len(errors)}")
        return 1
    print("Доступ к базе: сессии из композиции, SQL в репозиториях")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
