#!/usr/bin/env python3
"""Тот же снимок дерева не проверяют дважды.

`make precommit` в большом проекте идёт минутами, а прогоняется он подряд два
раза почти всегда: сначала руками (агентом или человеком), потом — хуком
`pre-commit` при самом `git commit`, на ровно том же содержимом. Индексация
файлы на диске не меняет, значит второй прогон гарантированно повторяет первый
и не может узнать ничего нового. Третий случай тот же по природе: повторный
запуск «на всякий случай», когда с прошлого раза не тронуто ничего.

Обёртка считает отпечаток входа, и если такой отпечаток уже помечен зелёным —
команду не запускает вовсе.

**Ключ считается по СОДЕРЖИМОМУ файлов, а не по диффу.** Соблазн взять
`git diff`, как это делает ревью-гейт, здесь ошибочен и опасен: `git diff` не
показывает untracked-файлы. Новый модуль на триста строк, ещё не добавленный в
индекс, не изменил бы отпечаток — и кэш отдал бы «зелёное» про файл, которого
ни mypy, ни pytest в глаза не видели. Ровно эту дыру в ревью-гейте затыкает
`STAGING_RE`, но там цена — правка мимо линз, а здесь была бы выдуманная
проверка. Поэтому берутся все файлы, которые git считает частью проекта:
отслеживаемые плюс неотслеживаемые и не игнорируемые.

Игнорируемое git'ом не учитывается вовсе — иначе кэш не сработал бы НИ РАЗУ:
`.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `.coverage` переписываются самим
же прогоном, и каждый прогон отменял бы предыдущий. Даётся это даром:
`--exclude-standard` у `git ls-files` — те же правила, что у `.gitignore`.

**CRLF приводится к LF.** У репозитория с `core.autocrlf=true` (Windows по
умолчанию) `git checkout`, `git stash` и `git stash pop` переписывают переносы
строк в рабочем дереве, git при этом честно считает файл неизменным. Без
нормализации ключ съезжал бы от операций, ничего не меняющих по смыслу, — а
`pre-commit` как раз стэшит неиндексированное вокруг своего запуска, то есть
кэш разваливался бы ровно в том сценарии, ради которого заведён. На исход
проверок это не влияет: ruff по умолчанию сохраняет переносы, какие в файле
есть, а не требует конкретных.

В ключ входит и окружение — версия питона и список установленных пакетов с
версиями. Обновился mypy или ruff — то же дерево законно даёт другой ответ, и
старое зелёное к нему не относится. Список пакетов читается из самого
интерпретатора, а не из `poetry.lock`: `pip install -U mypy` руками лока не
меняет.

Чего в ключе нет и быть не может: живости Docker, версии образа postgres,
состояния сети. Против этого работает только срок годности записи
(`ttl_hours`).

**Кэшируется исключительно успех.** Красное не запоминается никогда — иначе
после исправления одной строки нечем было бы перепроверить. Это же означает
известный перекос: флакающий тест, однажды позеленевший, застывает зелёным на
этом дереве до первой правки. Знать про это надо, а лечится оно не кэшем.

Дерево, изменившееся ВО ВРЕМЯ прогона, не запоминается: правка, приехавшая под
руку тестам, проверку не проходила. Исключение одно и названо в конфиге —
файлы, которые прогон правит сам. Такой у нас `.coverage-baseline`: сторож
покрытия поднимает планку до достигнутого. Запрет кэшировать после него стоил
бы полного лишнего прогона на каждый рост покрытия, а перепроверять там нечего
— планка встала ровно на тот процент, который только что показал зелёный
прогон. Поэтому запоминается дерево, которое лежит на диске ПОСЛЕ прогона.
Изменись вместе с планкой хоть один посторонний файл — не запоминается ничего.

**Вывод команды не перехватывается.** Дочерний процесс наследует stdout и
stderr как есть, код возврата отдаётся наружу без изменений — золотое правило 7
CLAUDE.md («вывод make не обрезают и не фильтруют») тут не нарушается, хотя
снаружи запуск make из питона на него похож. Ни одного конвейера здесь нет.

Про попадание сообщается ГРОМКО и с датой. Отчёт «precommit зелёный» без слова
«из кэша» — это и есть тот самый выдуманный результат, от которого предостерегает
то же правило; чтобы так сказать, надо не заметить пять строк на экране.

Запуск (см. цель `precommit` в Makefile):

    python scripts/precommit_cache.py <область> [--force] :: <команда...>

Разделитель `::`, а не привычное `--`: вызывают-то скрипт через `poetry run`, а
`poetry` разбирает свою командную строку сама и `--` из неё ВЫРЕЗАЕТ — до
скрипта он не доезжает. Голое `--` тоже принимается, для запуска мимо poetry.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural, tool_config  # noqa: E402

CACHE_DIR = ROOT / ".make-cache"
CACHE_FILE = CACHE_DIR / "green.json"

# Сколько зелёных отпечатков держать. Больше десятка бессмысленно: попадание
# бывает в последний-два, остальное — история, которую никто не читает.
KEEP_ENTRIES = 12

DEFAULT_TTL_HOURS = 24

# Пути, которые ни одна проверка из precommit не читает. Список чёрный, а не
# белый, и это осознанно: незнакомый новый файл обязан считаться входом по
# умолчанию. Белый список («считать только app, tests, scripts») молчал бы про
# `config/rules.yaml`, который завтра начнёт читать новый сторож.
#
# Заведёшь проверку, читающую документацию, — вычеркни `docs/**` отсюда.
DEFAULT_IGNORE = ("docs/**", "*.md", ".claude/**", ".idea/**", ".vscode/**")

# Файлы, которые прогон правит сам по себе. Их изменение во время прогона не
# отменяет запись в кэш — запоминается дерево, каким оно стало.
DEFAULT_SELF_UPDATING = (".coverage-baseline",)

# Отметка «файла на месте нет»: удалён, либо на его месте каталог (подмодуль).
# Только ASCII — bytes-литерал другого не принимает.
ABSENT = b"<absent>"

USAGE = "python scripts/precommit_cache.py <область> [--force] :: <команда...>"

# `--` понимается для запуска мимо poetry; в самом Makefile стоит `::`, потому
# что `poetry run` вырезает `--` из аргументов на своей стороне.
SEPARATORS = ("::", "--")


def config() -> dict:
    return tool_config("precommit_cache")


def ttl_seconds() -> float:
    return float(config().get("ttl_hours", DEFAULT_TTL_HOURS)) * 3600


def listed(key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    declared = config().get(key)
    return tuple(str(item) for item in declared) if isinstance(declared, list) else fallback


def ignore_patterns() -> tuple[str, ...]:
    return listed("ignore", DEFAULT_IGNORE)


def self_updating_patterns() -> tuple[str, ...]:
    return listed("self_updating", DEFAULT_SELF_UPDATING)


def is_ignored(path: str, patterns: tuple[str, ...]) -> bool:
    """Совпадение по `fnmatch`, где `*` пересекает `/`.

    То есть `*.md` ловит `docs/rules/шаблон.md`, а не только `шаблон.md` в
    корне. Для чёрного списка это ровно то поведение, которое нужно, но при
    правке списка помнить об этом обязательно.
    """
    return any(fnmatch(path, pattern) for pattern in patterns)


def git(*args: str) -> bytes:
    """Сырые байты вывода git.

    `core.quotepath=false` — иначе не-ASCII в путях приезжает экранированным
    восьмеричными последовательностями, и файл `app/сервис.py` открывался бы по
    несуществующему имени: содержимое в отпечаток не попало бы, а правка в нём
    не сбрасывала бы кэш.
    """
    result = subprocess.run(
        ["git", "-C", str(ROOT), "-c", "core.quotepath=false", *args],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=True,
    )
    return result.stdout


def tree_paths(patterns: tuple[str, ...]) -> list[str]:
    """Всё, что git считает частью проекта: отслеживаемое + новое неигнорируемое.

    Одним вызовом, а не двумя: `--cached --others --exclude-standard` даёт обе
    половины сразу, а `set` снимает возможный повтор.
    """
    raw = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    found = {path for path in raw.decode("utf-8", "surrogateescape").split("\0") if path}
    return sorted(path for path in found if not is_ignored(path, patterns))


def environment_marks() -> list[str]:
    """Версия интерпретатора и все установленные пакеты с версиями."""
    from importlib.metadata import distributions

    marks = [f"python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"]
    packages = set()
    for dist in distributions():
        try:
            packages.add(f"{dist.name}=={dist.version}")
        except Exception:  # noqa: BLE001 — битые метаданные соседа не наше дело
            continue
    marks.extend(sorted(packages))
    return marks


def file_marks() -> dict[str, bytes]:
    """Отметка на файл. Пофайлово, а не одним хэшем, — чтобы уметь ответить,
    ЧТО изменилось во время прогона: от этого зависит, можно ли запоминать.

    Каталог на месте файла (подмодуль — это gitlink) читается как отсутствующий:
    содержимое подмодуля в отпечаток не входит. Для шаблона это ничего не
    значит — подмодулей в нём нет; проекту с подмодулем, который проверяется
    precommit'ом, придётся дописать сюда обход.
    """
    marks: dict[str, bytes] = {}
    for path in tree_paths(ignore_patterns()):
        try:
            data = (ROOT / path).read_bytes()
        except OSError:
            # Удалённый, но ещё числящийся в индексе файл; либо каталог-gitlink.
            # Отметка обязана быть: удаление файла — тоже изменение.
            marks[path] = ABSENT
            continue
        marks[path] = hashlib.sha256(data.replace(b"\r\n", b"\n")).digest()
    return marks


def fingerprint(scope: str, marks: dict[str, bytes]) -> str:
    """Отпечаток по ГОТОВЫМ отметкам и текущему окружению.

    Отдельно от снятия отметок, потому что зовут его дважды и по-разному: один
    раз по свежему дереву, другой — по отметкам из старой записи, чтобы
    отличить «изменились файлы» от «изменилось окружение».
    """
    digest = hashlib.sha256()
    digest.update(f"область\0{scope}\0".encode())
    for mark in environment_marks():
        digest.update(f"окружение\0{mark}\0".encode())
    for path in sorted(marks):
        digest.update(path.encode("utf-8", "surrogateescape") + b"\0" + marks[path])
    return digest.hexdigest()[:16]


def snapshot(scope: str) -> tuple[str, dict[str, bytes]]:
    """`(отпечаток, пофайловые отметки)`."""
    marks = file_marks()
    return fingerprint(scope, marks), marks


def changed_between(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def changed_status(before: dict[str, bytes], after: dict[str, bytes]) -> list[tuple[str, str]]:
    """То же, но со статусом: `A` добавлен, `M` изменён, `D` исчез.

    Статус нужен отбору тестов: исчезнувший файл — это отдельный триггер.
    Пропавшую проверку не видно ни в одной карте зависимостей: у удалённого
    кода нет строк, которые кто-то мог бы исполнить.

    **Исчезновение — это `ABSENT`, а не отсутствие ключа**, и проверять надо
    именно так. Удалённый файл, который ещё не проиндексировали, `git ls-files
    --cached` продолжает называть: он есть в индексе. Отметка у него `ABSENT`,
    ключ на месте, и проверка «пути нет в словаре» считала бы его ИЗМЕНЁННЫМ —
    то есть триггер на удаление не срабатывал бы ровно в самом частом случае:
    удалил файл, запустил проверки, ещё не коммитил. Поймано опытом: подмена
    файла давала «изменился не питоновский файл» вместо «файл исчез».
    """
    rows: list[tuple[str, str]] = []
    for path in changed_between(before, after):
        if after.get(path, ABSENT) == ABSENT:
            rows.append(("D", path))
        elif before.get(path, ABSENT) == ABSENT:
            rows.append(("A", path))
        else:
            rows.append(("M", path))
    return rows


def listing(paths: list[str], limit: int = 5) -> str:
    """Имена через запятую, но не все подряд.

    Сорвавшийся `git checkout` посреди прогона меняет разом сотню файлов, и
    отчёт из ста путей в одну строку — это не отчёт, а стена, в которой не
    видно ни первого имени, ни того, сколько их всего.
    """
    if len(paths) <= limit:
        return ", ".join(paths)
    tail = len(paths) - limit
    return f"{', '.join(paths[:limit])} и ещё {plural(tail, 'файл', 'файла', 'файлов')}"


def latest_entry(entries: list[dict], scope: str) -> dict | None:
    """Последняя запись о зелёном прогоне этой области.

    Нужна не кэшу (ему хватает совпадения ключа), а отбору тестов: чтобы
    ответить «что изменилось с тех пор, как всё было проверено», надо знать не
    хэш того дерева, а его состав.
    """
    fitting = [entry for entry in entries if entry.get("scope") == scope and entry.get("marks")]
    return max(fitting, key=lambda entry: float(entry.get("saved_at", 0)), default=None)


def stored_marks(entry: dict) -> dict[str, bytes]:
    return {path: bytes.fromhex(value) for path, value in entry["marks"].items()}


def changed_lines(scope: str, marks: dict[str, bytes]) -> list[str]:
    """Либо один `FULL:<причина>`, либо строки `<статус>\\t<путь>`."""
    entry = latest_entry(load_entries(), scope)
    if entry is None:
        return ["FULL:нет записи о зелёном прогоне"]
    if time.time() - float(entry.get("saved_at", 0)) >= ttl_seconds():
        return ["FULL:запись о зелёном прогоне просрочена"]

    before = stored_marks(entry)
    # Ключ пересчитывается по СТАРЫМ отметкам, но текущим окружением: разойдётся
    # с записанным — значит изменились версии пакетов, а не файлы. Тогда прежние
    # зелёные тесты ни о чём не говорят, сколько бы файлов ни осталось на месте.
    if fingerprint(scope, before) != entry.get("key"):
        return ["FULL:изменился состав установленных пакетов"]
    return [f"{status}\t{path}" for status, path in changed_status(before, marks)]


def publish_changed(scope: str, marks: dict[str, bytes]) -> None:
    """Один ответ на прогон вместо ответа на каждого спрашивающего.

    Спрашивают двое — отбор тестов и `schema-check`, — и каждый вопрос стоит не
    только снятия отпечатка (секунда-две, на холодном диске больше), но и двух
    запусков `poetry run`. На прогоне, который затевался ради экономии минут,
    это десятки секунд впустую.

    Передаётся переменной окружения, а не просто файлом: путь в переменной
    означает «посчитано ЭТИМ прогоном». Файл сам по себе не сказал бы, от какого
    он дерева, и однажды ответил бы про вчерашнее.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / "changed.txt"
    path.write_text("\n".join(changed_lines(scope, marks)), encoding="utf-8")
    os.environ["PRECOMMIT_CHANGED"] = str(path)


def report_changed(scope: str) -> int:
    """Что изменилось с прошлого ЗЕЛЁНОГО прогона. Печатает по пути на строку.

    База отсчёта — не `HEAD` и не индекс, а последнее дерево, про которое
    известно, что оно прошло проверки. Разница принципиальная: после коммита
    рабочее дерево чистое, и `git diff` сказал бы «ничего не менялось» про
    правки, которые никто не проверял. А если зелёный прогон был три коммита
    назад — сюда попадут изменения всех трёх.

    Нет записи или изменилось окружение — `FULL` с причиной: отбор в таких
    условиях не имеет базы, а безопасная сторона у отбора — прогнать всё.
    """
    try:
        marks = file_marks()
    except Exception as error:  # noqa: BLE001 — нет git, не репозиторий, что угодно
        print(f"FULL:снимок дерева не снялся ({type(error).__name__}: {error})")
        return 0

    for line in changed_lines(scope, marks):
        print(line)
    return 0


def load_entries() -> list[dict]:
    try:
        stored = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        entries = stored["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def save_entries(entries: list[dict]) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps({"entries": entries[-KEEP_ENTRIES:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "меньше минуты назад"
    if minutes < 60:
        return plural(minutes, "минуту", "минуты", "минут") + " назад"
    hours = minutes // 60
    if hours < 24:
        return plural(hours, "час", "часа", "часов") + " назад"
    return plural(hours // 24, "день", "дня", "дней") + " назад"


def took(seconds: float) -> str:
    total = int(seconds)
    return f"{total} с" if total < 60 else f"{total // 60} мин {total % 60} с"


def report_hit(scope: str, key: str, files: int, entry: dict) -> None:
    saved_at = float(entry.get("saved_at", 0))
    stamp = time.strftime("%d.%m %H:%M", time.localtime(saved_at))
    print(
        f"ИЗ КЭША: `{scope}` не выполнялся — это дерево уже было зелёным.\n"
        f"  ключ    {key} ({plural(files, 'файл', 'файла', 'файлов')} + версии пакетов)\n"
        f"  снят    {stamp}, {ago(time.time() - saved_at)}; занял {took(entry.get('duration', 0))}\n"
        "  С тех пор не изменилось ни содержимое файлов, ни окружение.\n"
        "  Прогнать всё равно: FORCE=1 (например `make precommit FORCE=1`).\n"
        "  Докладывая результат, это слово — «из кэша» — обязательно назови."
    )


def run(command: list[str]) -> tuple[int, float]:
    """Команда как есть: вывод наследуется, код возврата отдаётся наружу."""
    started = time.monotonic()
    code = subprocess.call(command)
    return code, time.monotonic() - started


def main(argv: list[str]) -> int:
    # `changed` — справка для отбора тестов, а не запуск команды: печатает, что
    # изменилось с прошлого зелёного прогона. Здесь, а не отдельным скриптом,
    # потому что база отсчёта — запись этого самого кэша.
    if argv and argv[0] == "changed":
        return report_changed(argv[1] if len(argv) > 1 else "precommit")

    force = "--force" in argv or os.environ.get("PRECOMMIT_CACHE") == "off"
    argv = [arg for arg in argv if arg != "--force"]
    edges = [index for index, arg in enumerate(argv) if arg in SEPARATORS]
    if not edges:
        print(f"ОШИБКА ВЫЗОВА: нет разделителя `::`.\n  {USAGE}")
        return 2
    edge = edges[0]
    scope = " ".join(argv[:edge]).strip()
    command = argv[edge + 1 :]
    if not scope or not command:
        print(f"ОШИБКА ВЫЗОВА: нужны и область, и команда.\n  {USAGE}")
        return 2

    try:
        key, marks = snapshot(scope)
    except Exception as error:  # noqa: BLE001 — нет git, не репозиторий, что угодно
        print(
            f"КЭШ НЕ РАБОТАЕТ ({type(error).__name__}: {error}).\n"
            "  Прогон идёт полностью — сторона отказа безопасная."
        )
        return run(command)[0]

    alive = [
        entry for entry in load_entries() if time.time() - float(entry.get("saved_at", 0)) < ttl_seconds()
    ]
    if not force:
        for entry in alive:
            if entry.get("key") == key and entry.get("scope") == scope:
                report_hit(scope, key, len(marks), entry)
                return 0

    publish_changed(scope, marks)
    code, duration = run(command)
    if code != 0:
        # Красное не запоминается никогда: иначе первая же правка осталась бы
        # непроверяемой, а «почини и перезапусти» — единственный способ выйти
        # из красного состояния.
        return code

    after_key, after_marks = snapshot(scope)
    if after_key != key:
        touched = changed_between(marks, after_marks)
        self_updating = self_updating_patterns()
        if touched and all(is_ignored(path, self_updating) for path in touched):
            # Прогон поправил только то, что правит сам (планка покрытия).
            # Запоминается дерево, которое теперь на диске: перепроверять его
            # нечего, а запрет стоил бы лишнего полного прогона на каждый рост
            # покрытия.
            print(f"\nКЭШ: прогон сам обновил {listing(touched)} — запоминается дерево после него.")
            key, marks = after_key, after_marks
        else:
            print(
                f"\nКЭШ: зелёное НЕ записано — дерево изменилось во время прогона "
                f"({key} → {after_key}).\n"
                f"  Изменилось: {listing(touched) if touched else 'окружение (состав пакетов)'}\n"
                "  Запись означала бы «проверено» для содержимого, которого проверка не видела."
            )
            return 0

    alive = [
        entry for entry in alive if not (entry.get("scope") == scope and entry.get("key") == key)
    ]
    alive.append(
        {
            "scope": scope,
            "key": key,
            "files": len(marks),
            "saved_at": time.time(),
            "duration": duration,
            # Не только хэш, но и СОСТАВ дерева: по нему отбор тестов отвечает
            # «что изменилось с тех пор, как всё было проверено». Хэша для
            # этого не хватает — он говорит только «то же или не то же».
            "marks": {path: mark.hex() for path, mark in marks.items()},
        }
    )
    try:
        save_entries(alive)
    except OSError as error:
        print(f"\nКЭШ: записать не удалось ({error}). На результат прогона это не влияет.")
        return 0
    print(f"\nКЭШ: зелёное записано — ключ {key}, прогон занял {took(duration)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
