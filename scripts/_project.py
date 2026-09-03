"""Где у проекта исходники — один ответ на всех сторожей.

Зашитый константой каталог — худшая форма непереносимости: `rglob` по
несуществующему пути пуст, и сторож печатает «OK» с нулевым кодом. Отсюда два
правила: корень из `[tool.code_layout].source_root`, а пустой скан — ОТКАЗ.
"""

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
_DEFAULT_SOURCE_ROOT = "app"

# Один символ вне кодировки консоли роняет сторожа `UnicodeEncodeError`
# посреди отчёта, и это выглядит падением проверки, хотя ломается вывод.
# Меняем не кодировку, а реакцию на непредставимый символ. Здесь, потому что
# модуль импортируют все, — защита появляется у нового скрипта сама.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


def pyproject() -> dict:
    if not PYPROJECT.is_file():
        return {}
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def tool_config(section: str) -> dict:
    return pyproject().get("tool", {}).get(section, {})


def project_name() -> str:
    """Читаются оба места: переезд на `[project]` не должен молча отключать проверки."""
    raw = pyproject()
    declared = raw.get("project", {}).get("name") or raw.get("tool", {}).get("poetry", {}).get("name")
    return str(declared or "")


def require_dir(path: Path, setting: str) -> Path:
    """Названный в конфиге каталог обязан быть: тихий `continue` — та же болезнь."""
    if not path.is_dir():
        try:
            shown = path.relative_to(ROOT).as_posix()
        except ValueError:  # pragma: no cover — путь вне проекта
            shown = str(path)
        print(
            f"ОШИБКА НАСТРОЙКИ: каталога `{shown}` нет, а он назван в {setting}.\n"
            "  Проверка не может быть пройдена — ей просто нечего смотреть.\n"
            "  Поправь путь в pyproject.toml или заведи каталог."
        )
        sys.exit(2)
    return path


# `Repo`/`Repository`, за которыми НЕ идёт строчная буква. Не `(?=[A-Z]|$)`:
# предикату дают разобранную аннотацию, и `UserRepo | None` отвергалось бы.
# Множественное число обязательно, иначе сторожа расходятся на `UserRepos`.
_REPOSITORY_NAME = re.compile(r"(?:Repositories|Repository|Repos|Repo)(?![a-z])")
_REPOSITORY_TAIL = re.compile(r"(?:Repositories|Repository|Repos|Repo)$")


def is_repository_port(name: str) -> bool:
    """Сам репозиторий, а не то, что его отдаёт: `...RepoFactory` живёт по своей роли."""
    return bool(_REPOSITORY_TAIL.search(name))


def names_repository(name: str) -> bool:
    """Один предикат на всех, кто опознаёт репозиторий по имени: иначе расходятся."""
    return bool(_REPOSITORY_NAME.search(name))


def plural(count: int, one: str, few: str, many: str) -> str:
    """`1 голова`, `2 головы`, `5 голов`: отчёт с «5 головы» перестают читать."""
    tail, hundred = count % 10, count % 100
    if tail == 1 and hundred != 11:
        return f"{count} {one}"
    if 2 <= tail <= 4 and not 12 <= hundred <= 14:
        return f"{count} {few}"
    return f"{count} {many}"


def source_root() -> Path:
    """Каталог исходников. Нет каталога — громкий отказ, а не тихое «OK»."""
    configured = str(tool_config("code_layout").get("source_root", _DEFAULT_SOURCE_ROOT))
    return require_dir(ROOT / configured, "[tool.code_layout].source_root")
