"""Где у проекта исходники — один ответ на всех сторожей.

Каталог был зашит константой `ROOT / "app"` в семи скриптах, и это худшая
форма непереносимости из возможных: в проекте с другой раскладкой `rglob` по
несуществующему каталогу возвращает пустоту, скрипт печатает **«OK» и выходит
с нулём**. Пользователь копирует шаблон, меняет раскладку, `make precommit`
зелёный — и половина проверок мертва, причём отчёт выглядит пройденным.

Поэтому здесь два правила.

1. Корень исходников приходит из конфига (`[tool.code_layout].source_root`).
2. **Пустой скан — это отказ, а не успех.** Если каталога нет, скрипт обязан
   упасть громко: «правило ссылается в пустоту» — это ошибка настройки, и
   молчать о ней нельзя.
"""

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
_DEFAULT_SOURCE_ROOT = "app"


def tool_config(section: str) -> dict:
    """Секция `[tool.<section>]` из pyproject.toml."""
    if not PYPROJECT.is_file():
        return {}
    raw = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return raw.get("tool", {}).get(section, {})


def require_dir(path: Path, setting: str) -> Path:
    """Каталог, названный в конфиге, обязан существовать.

    Отдельной функцией, а не `if not base.exists(): continue` по месту: тихий
    `continue` — ровно та болезнь, от которой заведён этот модуль, просто
    спрятанная на строку глубже. Проект, переехавший с `interface/api/routes`
    на своё имя и забывший поправить конфиг, получал бы бодрое «OK» от
    проверки, которой нечего смотреть. Путь назвали в настройке — значит его
    наличие часть настройки, а не догадка.
    """
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


def source_root() -> Path:
    """Каталог исходников. Нет каталога — громкий отказ, а не тихое «OK»."""
    configured = str(tool_config("code_layout").get("source_root", _DEFAULT_SOURCE_ROOT))
    return require_dir(ROOT / configured, "[tool.code_layout].source_root")
