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

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
_DEFAULT_SOURCE_ROOT = "app"

# Windows-консоль по умолчанию отдаёт Python кодировку под локаль (здесь
# cp1251), и один символ вне неё — стрелка, тире, рамка — роняет сторожа
# `UnicodeEncodeError` посреди печати отчёта. Отказ при этом выглядит как
# падение проверки, хотя проверка прошла: ломается вывод, а не правило.
#
# Кодировку не подменяем (иначе кириллица приедет мохнатой), меняем только
# реакцию на непредставимый символ: он станет `?`, а не концом процесса.
# Ставится здесь, потому что этот модуль импортируют все сторожа, — то есть
# защита появляется у нового скрипта сама, без строки-напоминания в нём.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


def pyproject() -> dict:
    """Весь pyproject.toml разобранным."""
    if not PYPROJECT.is_file():
        return {}
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def tool_config(section: str) -> dict:
    """Секция `[tool.<section>]` из pyproject.toml."""
    return pyproject().get("tool", {}).get(section, {})


def project_name() -> str:
    """Имя проекта. Пусто, если pyproject его не объявляет.

    Читаются оба места: `[project]` (PEP 621) и `[tool.poetry]`. Шаблон
    объявляет имя вторым способом, но сторожу это знать незачем — проект,
    переехавший на первый, не должен из-за этого молча перестать проверяться.
    """
    raw = pyproject()
    declared = raw.get("project", {}).get("name") or raw.get("tool", {}).get("poetry", {}).get("name")
    return str(declared or "")


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


# `Repo` или `Repository`, за которыми НЕ идёт строчная буква.
#
# Именно так, а не `(?=[A-Z]|$)`: предикату скармливают не голое имя класса, а
# разобранную аннотацию — `ast.unparse` отдаёт `UserRepo | None` и
# `list[UserRepo]`, и требование «заглавная или конец» отвергало обе. Вход,
# попросивший `FromDishka[UserRepo | None]`, проходил бы мимо проверки молча.
# Запрет одной лишь строчной буквы отсекает `Report` (ради чего граница и
# нужна) и пропускает любой разделитель, какой встретится в аннотации.
#
# Множественное число входит в набор, иначе три сторожа расходятся на нём:
# `repository_markers` в `[tool.query_loops]` содержит `repos` и
# `repositories`, то есть для `check_n_plus_one` `UserRepos` — репозиторий, а
# для этих двух не был. Порт во множественном числе можно было внедрить прямо
# во вход, и оба молчали. Порядок в чередовании от длинного к короткому:
# иначе `Repo` съел бы начало `Repository` и лукахед отверг бы всё слово.
_REPOSITORY_NAME = re.compile(r"(?:Repositories|Repository|Repos|Repo)(?![a-z])")
_REPOSITORY_TAIL = re.compile(r"(?:Repositories|Repository|Repos|Repo)$")


def is_repository_port(name: str) -> bool:
    """Сам репозиторий, а не то, что его отдаёт.

    `UserRepo` — прямой порт, ему место в `ports/repositories/`.
    `PortalAnchorTemplateRepoFactory` и `SubscriptionRepoByPortal` репозиторий
    отдают, но сами им не являются, и живут по своей роли — с них расположение
    не спрашивают.
    """
    return bool(_REPOSITORY_TAIL.search(name))


def names_repository(name: str) -> bool:
    """Говорит ли имя типа, что за ним репозиторий.

    Один предикат на всех, кто опознаёт репозиторий ПО ИМЕНИ ТИПА, — иначе
    сторожа разъезжаются молча: `check_composition` отбивал `*Repo` во входе
    строгим окончанием, `check_db_access` требовал того же от порта, и
    `SubscriptionRepoByPortal` был репозиторием для одного и не был для
    другого.

    Граница слова обязательна и в конце, и в середине. По окончанию —
    `PortalAnchorTemplateRepoFactory` и `SubscriptionRepoByPortal` перестают
    считаться репозиториями, хотя отдают именно его. По подстроке — им
    становится `ReportService`, потому что `Repo` живёт внутри `Report`.
    """
    return bool(_REPOSITORY_NAME.search(name))


def plural(count: int, one: str, few: str, many: str) -> str:
    """`1 голова`, `2 головы`, `5 голов`.

    Здесь, а не по месту: согласование числительного нужно любому сторожу,
    который печатает счёт находок, и вторая копия этих четырёх строк разойдётся
    с первой ровно тогда, когда кто-то поправит одну. Отчёт с «5 головы»
    выглядит машинным — а сторожа тут читают внимательно только пока верят,
    что их писал человек.
    """
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
