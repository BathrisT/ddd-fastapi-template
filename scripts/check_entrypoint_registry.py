"""Точка входа, не попавшая в свой реестр, — это тихая потеря сообщения.

Явный реестр лучше регистрации побочным эффектом импорта: список видно,
порядок не зависит от того, кто кого импортировал первым. Но ведут его руками,
и он расходится с каталогом обработчиков — дописал файл, забыл строку. Отказа
при этом нет: обработчик просто не существует для очереди, а отправитель
кладёт сообщение с именем, которое никто не разберёт. Так уже терялись две
задачи, и заметили это только на ревью.

Обратную сторону (реестр называет то, чего нет) ловит сам Python на импорте.

Про taskiq проверка не знает: «каталог входов» и «файл, который обязан их
назвать» приходят из `[tool.entrypoint_registry]`. Тем же правилом
закрывается любой ручной список — маршрутизатор событий, реестр команд.
"""

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _config() -> dict:
    raw = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return raw.get("tool", {}).get("entrypoint_registry", {})


def _public_functions(directory: Path) -> list[tuple[str, str]]:
    """Публичные функции уровня модуля во всех файлах каталога: (имя, где).

    Список, а не словарь: два одноимённых входа схлопывались в один, и второй
    наследовал регистрацию первого — а для очереди тёзка ещё и затирает её.
    """
    found: list[tuple[str, str]] = []
    for path in sorted(directory.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name.startswith("_"):
                continue
            found.append((node.name, f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"))
    return found


def _named_in(registry: Path) -> set[str]:
    """Любое имя, произнесённое реестром.

    Форму записи не навязываем: требовать синтаксис значило бы запретить
    рефакторинг реестра. Вопрос один — названо или забыто.
    """
    try:
        tree = ast.parse(registry.read_text(encoding="utf-8"), filename=str(registry))
    except (SyntaxError, OSError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def check_entries_are_registered(config: dict) -> list[str]:
    errors: list[str] = []
    for pair in config.get("pairs", []):
        entries_dir = ROOT / str(pair.get("entries", ""))
        registry = ROOT / str(pair.get("registry", ""))
        if not entries_dir.is_dir() or not registry.is_file():
            errors.append(
                f"[tool.entrypoint_registry]: не найден каталог входов "
                f"`{pair.get('entries')}` или реестр `{pair.get('registry')}`"
            )
            continue

        named = _named_in(registry)
        entries = sorted(_public_functions(entries_dir))
        seen: dict[str, str] = {}
        for name, where in entries:
            if name in seen:
                errors.append(
                    f"{where}: вход `{name}` уже объявлен в {seen[name]}. "
                    "Два входа с одним именем неразличимы для отправителя: "
                    "регистрация одного затирает другого."
                )
            seen[name] = where
            if name in named:
                continue
            errors.append(
                f"{where}: вход `{name}` не назван реестром "
                f"{registry.relative_to(ROOT).as_posix()}. "
                "Незарегистрированный обработчик не падает — он просто не "
                "существует для отправителя, и сообщение теряется молча."
            )
    return errors


def main() -> int:
    config = _config()
    if not config:
        print("Entrypoint registry: [tool.entrypoint_registry] не настроен — проверка пропущена.")
        return 0
    errors = check_entries_are_registered(config)
    if errors:
        for error in errors:
            print(error)
        print(f"\n{len(errors)} вход(ов) мимо реестра.")
        return 1
    print("Entrypoint registry: каждый вход назван своим реестром. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
