"""Сосед по пакету не должен теряться: `__init__.py` упоминает всех рядом.

Болезнь конкретная: файл с маршрутами лежит в папке, а в агрегаторе его нет.
Ошибки сборки не будет — будет отсутствие ручки в проде, и найдётся оно, когда
её позовут. Тот же сорт, что был у воркера с импортами-ради-побочного-эффекта:
списки существовали руками и разошлись.

Проверяется УПОМИНАНИЕ, а не форма включения. Требовать «зарегистрируй всё
одинаково в один роутер» нельзя: выбор там законный и используется —
`routes/portals/__init__.py` включает публичный каталог типов сущностей мимо
авторизации, а девять остальных под ней; `routes/admin/__init__.py` держит
логин публичным, а три роутера под admin-JWT. Правило, требующее единообразия,
запретило бы ровно то место, где принимается решение о доступе.

Порядок включения тоже не наше дело: у FastAPI побеждает первый совпавший
путь, и переставлять роутеры — осознанная работа автора.

Конфиг — `[tool.package_coverage]` в pyproject.toml.
"""

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import require_dir  # noqa: E402


def _config() -> dict:
    raw = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return raw.get("tool", {}).get("package_coverage", {})


def _mentioned_names(init_path: Path) -> set[str]:
    """Соседи, которых `__init__.py` действительно называет.

    Импорт РЕЗОЛВИТСЯ в путь, а не угадывается по последнему сегменту.
    Сегмент сам по себе ничего не значит: `from app...guards.bitrix import
    get_verified_portal` — про чужой пакет, но кончается на «bitrix», и сосед
    `bitrix.py` засчитывался им как упомянутый. Так проверка молчала о живой
    ручке, ни разу не включённой в агрегатор.
    """
    directory = init_path.parent
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    except SyntaxError:
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # Относительный импорт отсчитывается от своего пакета, абсолютный —
            # от корня проекта. `level=1` — сам пакет, каждая следующая точка
            # поднимает на уровень выше.
            if node.level:
                base = directory
                for _ in range(node.level - 1):
                    base = base.parent
            else:
                base = ROOT
            package = base / Path(*node.module.split(".")) if node.module else base
            if package.parent == directory:
                # `from .blocks import router` — сосед назван самим путём
                names.add(package.name)
            elif package == directory:
                # `from . import blocks` — соседи стоят справа
                names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module = ROOT / Path(*alias.name.split("."))
                if module.parent == directory:
                    names.add(module.name)
    return names


def check_neighbours_are_mentioned(config: dict) -> list[str]:
    roots = config.get("packages", [])
    errors: list[str] = []

    for root in roots:
        base = require_dir(ROOT / root, "[tool.package_coverage].packages")
        for init_path in sorted(base.rglob("__init__.py")):
            directory = init_path.parent
            mentioned = _mentioned_names(init_path)

            neighbours = [p.stem for p in sorted(directory.glob("*.py")) if p.name != "__init__.py"]
            # Подкаталог считается соседом, даже если `__init__.py` в нём
            # забыли. Раньше такой каталог выпадал ДВАЖДЫ: и из списка соседей,
            # и из обхода по `__init__.py`, — то есть целая папка живых ручек
            # исчезала молча. Забыть `__init__.py` — частая случайность, и
            # последствие у неё ровно то, ради чего сторож заведён.
            neighbours += [
                d.name
                for d in sorted(directory.iterdir())
                if d.is_dir() and d.name != "__pycache__" and any(d.glob("*.py"))
            ]

            missing = [name for name in neighbours if name not in mentioned]
            if not missing:
                continue
            relative = init_path.relative_to(ROOT).as_posix()
            errors.append(
                f"{relative}: рядом лежит, но не упомянуто — {', '.join(sorted(missing))}. "
                "Файл, которого нет в агрегаторе, просто не поднимется, и это "
                "будет не ошибкой сборки, а отсутствием ручки в проде."
            )
    return errors


def main() -> int:
    config = _config()
    if not config:
        print("Package coverage: [tool.package_coverage] не настроен — проверка пропущена.")
        return 0
    errors = check_neighbours_are_mentioned(config)
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} пакет(ов) забыли соседа.")
        return 1
    print("Package coverage: агрегаторы знают про всех соседей. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
