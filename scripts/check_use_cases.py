"""Правила сценариев: один файл — один сценарий, и сценарии не композируются.

Первые три требования — про форму файла в `use_cases/`, последние два — про то,
кто имеет право сценарий вызывать. Проверка одна, потому что нарушение любого
из пяти означает одно и то же: в запросе выполняется больше одного сценария.

Три требования к файлу в `use_cases/`:

1. **Ровно один класс-сценарий, и имя кончается на `UseCase`.** Иначе в папке
   сценариев заводится что угодно: `GoalValidation`, `GoalKeyboard`,
   `SetupStages` — хелперы, которые сценариями не были и по слою проходили
   только потому, что их некуда было положить. Место такому — `services/`.

2. **Единственный публичный метод — `execute`.** Второй публичный метод
   означает второй сценарий: `StartReviewUseCase.comment_request` и
   `GoalActionsUseCase.{confirm,reject,extend}` были отдельными действиями
   бота, слипшимися в один класс ради общих зависимостей. Общие зависимости —
   повод завести сервис, а не повод склеить сценарии.

3. Носители данных (`@dataclass`-команды, енумы, pydantic-модели, Protocol,
   исключения) не в счёт: `CreatePlanCommand` рядом со своим сценарием — норма.

Требования 1–3 проверяются только в `use_cases/`. Сервис волен иметь сколько
угодно публичных методов — у него другая работа.

Ещё два требования смотрят на весь `app/`, потому что нарушить их можно откуда
угодно:

4. **Держишь чужой сценарий — не коммитишь сам.** Сессия одна на вход, поэтому
   `commit()` в классе, которому внедрили `*UseCase`, фиксирует заодно чужую
   недоделанную работу, а следом уходит событие — и воркер берётся за половину
   запроса раньше, чем вызывающий упадёт. Транзакцию откатить можно,
   поставленную задачу нельзя.

   Запрещено именно это, а не внедрение сценария как таковое. Проверка
   «`*UseCase` не может быть аргументом `__init__`» выглядит строже и была бы
   бесполезной: на боевом проекте, из которого вырос шаблон, она даёт 15
   срабатываний, и все до одного ложные. Законных потребителей чужого сценария
   там два, и оба своих записей не делают — **диспетчер** (`GoalsHandler` берёт
   девять сценариев и на апдейт зовёт ровно один) и **перечислитель**
   (`AdvancePortalGoalChainsUseCase` зовёт внутренний сценарий по каждому
   кандидату). Классов, которые держат сценарий и коммитят сами, там ноль: в
   этой формулировке правило фиксирует сложившуюся практику, а не запрещает
   работающий код.

5. **Вход просит не больше одного сценария.** Хендлер с двумя
   `FromDishka[*UseCase]` — тот же самый дефект, только собранный не через
   конструктор, а прямо в обработчике: два `commit()` на один запрос. Признак
   входа здесь не путь, а сам `FromDishka` — так проверка не промахнётся мимо
   каталога, заведённого завтра.

Оба требования про одно: **один `commit()` на вход**. Понадобится
переиспользовать не шаг, а последовательность шагов с событиями — это сигнал
переносить `commit()` на границу входа, и тогда оба снимаются целиком.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ast_shapes import is_data_carrier  # noqa: E402
from _project import ROOT, require_dir, source_root, tool_config  # noqa: E402


def _is_data_carrier(node: ast.ClassDef) -> bool:
    # Строгий режим: в каталоге сценариев `@dataclass` на классе с методами —
    # не носитель данных, а сценарий, спрятанный от проверки
    return is_data_carrier(node, strict=True)


_LAYOUT = tool_config("code_layout")
# Каталог сценариев, суффикс их имени и имя единственного метода — из конфига:
# в соседнем проекте это `interactors/`, `Interactor` и `handle`, и зашивать
# сюда местные слова значило бы раздать шаблон с проверкой, которая молчит.
USE_CASES = require_dir(
    source_root() / str(_LAYOUT.get("use_cases_dir", "application/use_cases")),
    "[tool.code_layout].use_cases_dir",
)
_SUFFIX = str(_LAYOUT.get("use_case_suffix", "UseCase"))
_ENTRY = str(_LAYOUT.get("use_case_entrypoint", "execute"))
_COMMIT = str(_LAYOUT.get("commit_method", "commit"))


def _public_methods(node: ast.ClassDef) -> list[str]:
    return [
        item.name
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        and not item.name.startswith("_")
    ]


def check_use_cases() -> list[str]:
    errors: list[str] = []
    for path in sorted(USE_CASES.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        relative = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue

        classes = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and not _is_data_carrier(node)
        ]
        scenarios = [node for node in classes if node.name.endswith(_SUFFIX)]
        strays = [node for node in classes if not node.name.endswith(_SUFFIX)]

        for node in strays:
            errors.append(
                f"{relative}:{node.lineno}: `{node.name}` — не сценарий. "
                f"Здесь живут только классы `*{_SUFFIX}`; хелперу место в services/"
            )
        if len(scenarios) > 1:
            names = ", ".join(node.name for node in scenarios)
            errors.append(f"{relative}: {len(scenarios)} сценария в одном файле ({names})")
        for node in scenarios:
            public = _public_methods(node)
            extra = [name for name in public if name != _ENTRY]
            if extra:
                errors.append(
                    f"{relative}:{node.lineno}: у `{node.name}` публичные методы "
                    f"помимо {_ENTRY}: {', '.join(sorted(extra))}. "
                    "Каждый — отдельный сценарий; общее вынеси в services/"
                )
            elif not public:
                errors.append(f"{relative}:{node.lineno}: у `{node.name}` нет `{_ENTRY}`")
    return errors


def _parsed(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover — синтаксис ловит линтер
        return None


def _annotation_text(annotation: ast.expr | None) -> str:
    """Текст аннотации без кавычек.

    Кавычки снимаются не для красоты: при `from __future__ import annotations`
    и в отложенных ссылках тип записан строковой константой, и правило,
    сравнивающее «как есть», молчало бы ровно в тех файлах, где аннотации
    отложены, — то есть выборочно и незаметно.
    """
    if annotation is None:
        return ""
    text = ast.unparse(annotation).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _injected_type(annotation: ast.expr | None) -> str:
    """Тип внутри `FromDishka[...]`, иначе пусто.

    Признак входа — сам `FromDishka`, а не каталог: так проверка не промахнётся
    мимо входа, заведённого в новой папке.
    """
    if not isinstance(annotation, ast.Subscript):
        return ""
    marker = annotation.value
    name = getattr(marker, "id", None) or getattr(marker, "attr", None)
    if name != "FromDishka":
        return ""
    return _annotation_text(annotation.slice)


def _args_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    return [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]


def _init_of(node: ast.ClassDef) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == "__init__":
            return item
    return None


def _calls_commit(node: ast.ClassDef) -> int:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr == _COMMIT:
                return sub.lineno
    return 0


def check_no_commit_around_scenario() -> list[str]:
    """Требование 4: держишь чужой сценарий — не коммитишь сам."""
    errors: list[str] = []
    for path in sorted(source_root().rglob("*.py")):
        tree = _parsed(path)
        if tree is None:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            init = _init_of(node)
            if init is None:
                continue
            scenarios = [
                requested
                for arg in _args_of(init)
                if arg.arg != "self" and (requested := _annotation_text(arg.annotation)).endswith(
                    _SUFFIX
                )
            ]
            if not scenarios:
                continue
            commit_line = _calls_commit(node)
            if not commit_line:
                continue
            errors.append(
                f"{relative}:{commit_line}: `{node.name}` получил сценарий "
                f"({', '.join(scenarios)}) и коммитит сам. Сессия одна на вход: этот "
                f"`{_COMMIT}()` зафиксирует чужую недоделанную работу, а следом уйдёт "
                f"событие — отозвать его нечем. Коммитит тот, чья работа"
            )
    return errors


def check_one_scenario_per_entry() -> list[str]:
    """Требование 5: вход просит не больше одного сценария."""
    errors: list[str] = []
    for path in sorted(source_root().rglob("*.py")):
        tree = _parsed(path)
        if tree is None:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            requested = [
                injected
                for arg in _args_of(node)
                if (injected := _injected_type(arg.annotation)).endswith(_SUFFIX) and injected
            ]
            if len(requested) > 1:
                errors.append(
                    f"{relative}:{node.lineno}: вход `{node.name}` просит "
                    f"{len(requested)} сценария ({', '.join(requested)}) — это два "
                    f"`commit()` на один вход и событие, ушедшее до конца работы. "
                    f"Один вход — один сценарий"
                )
    return errors


def main() -> int:
    errors = check_use_cases() + check_no_commit_around_scenario() + check_one_scenario_per_entry()
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} нарушений правил сценариев.")
        print("Один файл — один сценарий; чужой сценарий не коммитят; один commit() на вход.")
        return 1
    print("Use cases: один файл — один сценарий, один commit() на вход. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
