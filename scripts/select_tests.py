#!/usr/bin/env python3
"""Какие тесты гонять: только те, до которых изменения могли дотянуться.

Отбор не выводит зависимость, а спрашивает объявленную в
`[tool.test_selection]`: tach и testmon оба промахиваются мимо связи
`tests/unit/scripts/**` → `scripts/*.py`, потому что сторож запускается
дочерним процессом. Безопасная сторона у отбора — прогнать всё.

Отсеянное записывается, и полный прогон сверяет: соврало правило — его назовут
поимённо. Разбор целиком — docs/rules/кэш-и-отбор-прогона.md.

    python scripts/select_tests.py plan     # цели для pytest либо FULL:<причина>
    python scripts/select_tests.py verify   # сверить полный прогон с отсеянным
"""

import json
import os
import subprocess
import sys
import time
from fnmatch import fnmatch
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural, tool_config  # noqa: E402

CACHE_DIR = ROOT / ".make-cache"
JOURNAL = CACHE_DIR / "selection.jsonl"
JUNIT = CACHE_DIR / "full-run.xml"
SCOPE = "precommit"

# Держим ограниченное число решений: журнал очищается зелёным полным прогоном, и
# бесконечный рост означал бы, что полного прогона не было очень давно, — а это
# отдельная беда, которую копящийся файл не лечит.
KEEP_DECISIONS = 50


def config() -> dict:
    return tool_config("test_selection")


def zones() -> list[dict]:
    declared = config().get("zones")
    return [zone for zone in declared if isinstance(zone, dict)] if isinstance(declared, list) else []


def always_full() -> tuple[str, ...]:
    declared = config().get("always_full")
    return tuple(str(item) for item in declared) if isinstance(declared, list) else ()


def matches(path: str, patterns: object) -> bool:
    if not isinstance(patterns, list):
        return False
    return any(fnmatch(path, str(pattern)) for pattern in patterns)


def parse_changed(text: str) -> tuple[str, list[tuple[str, str]]]:
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line.startswith("FULL:"):
            return line[len("FULL:") :], []
        status, _, path = line.partition("\t")
        if path:
            rows.append((status.strip(), path))
    return "", rows


def changed_rows() -> tuple[str, list[tuple[str, str]]]:
    """`(причина полного прогона или пусто, [(статус, путь)])`.

    Готовый ответ от обёртки кэша, если он есть: путь к нему лежит в
    `PRECOMMIT_CHANGED`, и переменная означает «посчитано этим прогоном». Иначе
    (одиночный `make test-unit`) считаем сами, своим запуском.
    """
    published = os.environ.get("PRECOMMIT_CHANGED")
    if published:
        try:
            return parse_changed(Path(published).read_text(encoding="utf-8"))
        except OSError:
            pass  # посчитаем сами — сторона отказа рабочая, а не тихая

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "precommit_cache.py"), "changed", SCOPE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        check=False,
    )
    if result.returncode != 0:
        return f"справка об изменениях не собралась: {result.stderr.strip()[:200]}", []
    return parse_changed(result.stdout)


def decide(
    rows: list[tuple[str, str]], zone_list: list[dict], triggers: tuple[str, ...]
) -> tuple[str, list[str]]:
    """`(причина полного прогона или пусто, цели для pytest)`.

    Чистая функция от изменений и правил: конфиг читают вызывающие, поэтому
    решение «что не гонять» проверяется тестами напрямую. Проверять надо —
    ошибка здесь выглядит как зелёный прогон.
    """
    targets: set[str] = set()
    for status, path in rows:
        if status == "D":
            return f"файл исчез или переименован ({path})", []
        if matches(path, list(triggers)):
            return f"триггер полного прогона ({path})", []
        if not path.endswith(".py"):
            return f"изменился не питоновский файл ({path})", []
        for zone in zone_list:
            if matches(path, zone.get("paths")):
                targets.update(str(test) for test in zone.get("tests", []))
                break
        else:
            return f"файл вне известных зон ({path})", []
    return "", sorted(targets)


def narrow(targets: list[str], within: str) -> list[str]:
    """Пересечение отбора с подмножеством набора.

    Считается в ОБЕ стороны: цель может лежать внутри подмножества, а может
    его накрывать — и тогда гнать надо подмножество, а не цель.
    """
    edge = normal(within)
    kept: list[str] = []
    for target in targets:
        if covered_by(target, [edge]):
            kept.append(normal(target))
        elif covered_by(edge, [target]):
            kept.append(edge)
    return sorted(set(kept))


def watch_group(name: str) -> list[str]:
    declared = config().get("watch")
    group = declared.get(name) if isinstance(declared, dict) else None
    return [str(item) for item in group] if isinstance(group, list) else []


def touched(name: str) -> int:
    """Менялось ли хоть что-то из названной группы файлов.

    Для дорогих проверок с узким входом: `schema-check` поднимает Postgres, а
    зависит от миграций и ORM-моделей. Группа обязана быть непустой — пустая
    означала бы вечный пропуск дорогой проверки.
    """
    patterns = watch_group(name)
    if not patterns:
        print(f"YES:группа `{name}` в [tool.test_selection.watch] пуста или не объявлена")
        return 0

    reason, rows = changed_rows()
    if reason:
        print(f"YES:{reason}")
        return 0
    for _, path in rows:
        if matches(path, patterns):
            print(f"YES:менялся {path}")
            return 0
    print("NO")
    return 0


def remember(reason: str, targets: list[str], rows: list[tuple[str, str]], within: str = "") -> None:
    """Решение в журнал — чтобы полному прогону было с чем сверяться.

    ДОПИСЫВАНИЕМ строки, а не перезаписью: чтение-правка-запись целого JSON —
    три шага, и два параллельных `make` затрут решения друг друга. Пропавшее
    решение никто не сверит, то есть страховка отключится молча.
    """
    decisions = [
        {
            "at": time.time(),
            "reason": reason,
            "targets": targets,
            "changed": [path for _, path in rows],
            # Чей это был вопрос: весь набор или подмножество. Без этого решение
            # `--within tests/unit` выглядело бы как «интеграционные отсеяли», и
            # первое же законное падение интеграционного теста обвинило бы отбор
            # во лжи — сторож ложных тревог поднимал бы ложную тревогу.
            "within": normal(within),
        }
    ]
    CACHE_DIR.mkdir(exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as journal:
        for decision in decisions:
            journal.write(json.dumps(decision, ensure_ascii=False) + "\n")


def read_journal() -> list[dict]:
    """Последние решения. Битая строка пропускается, а не роняет сверку.

    Обрезка по числу — при чтении, а не при записи: писатель не имеет права
    трогать чужие строки, иначе вернётся та самая гонка, от которой ушли.
    """
    try:
        lines = JOURNAL.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    decisions: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue  # оборванная запись от убитого процесса — не повод падать
        if isinstance(item, dict):
            decisions.append(item)
    return decisions[-KEEP_DECISIONS:]


def plan(within: str = "") -> int:
    reason, rows = changed_rows()
    targets: list[str] = []
    if not reason:
        reason, targets = decide(rows, zones(), always_full())

    if reason:
        remember(reason, [], rows, within)
        print(f"FULL:{reason}")
        return 0
    if not rows:
        print("NOTHING")
        return 0

    if within:
        targets = narrow(targets, within)
    remember("", targets, rows, within)
    print(" ".join(targets) if targets else "NOTHING")
    return 0


def failed_files() -> list[tuple[str, str]] | None:
    """`(файл, имя теста)` для упавших. `None` — ОТЧЁТА НЕТ ВОВСЕ.

    Разница между `None` и `[]` — вся суть сторожа: пустой список значит
    «никто не упал», отсутствие файла — «прогона не было». Пока это было одним
    значением, сбой инфраструктуры СТИРАЛ журнал.
    """
    try:
        tree = ElementTree.parse(JUNIT)
    except (OSError, ElementTree.ParseError):
        return None
    broken: list[tuple[str, str]] = []
    for case in tree.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        path = (case.get("file") or "").replace("\\", "/")
        broken.append((path, case.get("name") or "?"))
    return broken


def normal(path: str) -> str:
    """Путь в одном виде: слэши, без ведущего `./`, без хвостового слэша.

    Не косметика: Makefile спрашивает `./tests/unit`, а зоны объявлены как
    `tests/unit/app`, и строковое сравнение давало «не пересекается» — то есть
    `make test-unit` сообщал «менять нечего», НЕ ЗАПУСКАЯ НИ ОДНОГО ТЕСТА.
    """
    cleaned = path.replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.rstrip("/")


def covered_by(path: str, targets: list[str]) -> bool:
    inside = normal(path)
    for target in targets:
        edge = normal(target)
        if edge and (inside == edge or inside.startswith(edge + "/")):
            return True
    return False


def verify() -> int:
    """Соврал ли отбор: упал ли тест, который до этого отсеяли.

    Зовётся после ПОЛНОГО прогона. Он прошёл — журнал очищается: всё, что
    отбор отсеивал с прошлого раза, проверено целиком.
    """
    decisions = read_journal()
    broken = failed_files()

    # Считаются только решения, где отбор действительно отсеивал. Решение «гнать
    # всё» ничего не отсеяло, и хвастаться его подтверждением — врать числом.
    selections = [decision for decision in decisions if not decision.get("reason")]

    if broken is None:
        # Журнал НЕ трогаем: отсеянное так и осталось непроверенным, и следующий
        # настоящий полный прогон обязан его увидеть.
        if selections:
            print(
                f"Отбор: верификация НЕ состоялась — полный прогон не оставил отчёта "
                f"({JUNIT.name}). Неподтверждённым осталось "
                f"{plural(len(selections), 'решение', 'решения', 'решений')} отбора, "
                "журнал сохранён до следующего настоящего прогона."
            )
        return 0

    if not broken:
        if selections:
            print(
                f"Отбор: полный прогон подтвердил "
                f"{plural(len(selections), 'решение', 'решения', 'решений')} — "
                "ни один отсеянный тест не падает. Журнал очищен."
            )
        JOURNAL.unlink(missing_ok=True)
        return 0

    lied: list[str] = []
    for path, name in broken:
        for decision in selections:
            within = str(decision.get("within") or "")
            if within and not covered_by(path, [within]):
                continue  # это решение про другую часть набора, оно тут ни при чём
            if not covered_by(path, decision.get("targets", [])):
                stamp = time.strftime("%d.%m %H:%M", time.localtime(float(decision.get("at", 0))))
                lied.append(
                    f"  {path}::{name}\n"
                    f"    отсеян решением от {stamp}; гнали: {', '.join(decision.get('targets', [])) or '—'}\n"
                    f"    менялось: {', '.join(decision.get('changed', [])) or '—'}"
                )
                break

    if not lied:
        return 0

    print(
        "\nОТБОР СОВРАЛ: полный прогон уронил тесты, которые выборочный не запускал.\n"
        + "\n".join(lied)
        + "\n\n"
        "  Это не «упал тест», это «правило отбора пропустило поломку». Правило —\n"
        "  `[tool.test_selection]` в pyproject.toml: у зоны, куда попали изменённые\n"
        "  файлы, не назван тот тест, который их проверяет. Почини правило, а не\n"
        "  только тест: молчаливый пропуск повторится на следующем таком диффе."
    )
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "plan"
    rest = sys.argv[2:]
    if command == "plan":
        # `--within tests/unit` — «ответь про эту часть набора»
        sys.exit(plan(rest[1] if len(rest) > 1 and rest[0] == "--within" else ""))
    if command == "touched":
        sys.exit(touched(rest[0] if rest else ""))
    if command == "verify":
        sys.exit(verify())
    print(
        f"неизвестная команда: {command}; ожидается plan, touched или verify",
        file=sys.stderr,
    )
    sys.exit(2)
