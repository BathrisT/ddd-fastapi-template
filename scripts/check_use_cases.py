"""Guard the shape of `app/application/use_cases/`: один файл — один сценарий.

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

Проверяется только `use_cases/`. Сервис волен иметь сколько угодно публичных
методов — у него другая работа.
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


def main() -> int:
    errors = check_use_cases()
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} нарушений формы use case'ов.")
        print("Один файл — один сценарий: класс `*UseCase` с единственным `execute`.")
        return 1
    print("Use cases: один файл — один сценарий с единственным execute. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
