"""Сторож, не названный в гейте, не существует.

Проверка не падает и не зеленеет — её просто нет, а выглядит это ровно как
пройденная: `make precommit` зелёный, файл в репозитории лежит, тесты на него
написаны. В соседнем боевом проекте так молчали пять сторожей сразу.

Критерий — ДОСТИЖИМОСТЬ из `make precommit`, а не упоминание в файле. Разница в
том самом случае, ради которого сторож и заведён: вызов, выведенный из гейта
комментарием, в тексте Makefile остаётся, и греп по нему показал бы зелёное.
Поэтому Makefile разбирается как граф целей: зависимости плюс вложенные вызовы
make (`$(MAKE)` и любая переменная, в которую он записан, — в этом проекте
`$(RECURSE)`), а закомментированные строки рецепта отбрасываются.

Заодно обратная сторона той же монеты: гейт, зовущий несуществующий файл.
Переименовали сторожа — `make` упадёт «No such file», но узнает об этом тот,
кто следующим запустит прогон, а не тот, кто переименовал.

Намеренное исключение возможно (дорогой сторож, живущий только в CI), но лежит
в `exempt` в `[tool.gate_wiring]`, то есть видно в ревью.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural, require_dir, tool_config  # noqa: E402

RULE = "docs/rules/проверяющий-контур.md"

DEFAULT_MAKEFILE = "Makefile"
DEFAULT_GUARDS_DIR = "scripts"
DEFAULT_GUARDS_GLOB = "check_*.py"
DEFAULT_ROOTS = ("precommit",)

# Присваивание проверяется ДО цели: `SHELL := $(GIT_BASH)` — не цель `SHELL`.
ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:+?]?=\s*(.*)$")
TARGET = re.compile(r"^([^\t#][^:=]*):(?!=)\s*(.*)$")
TOKEN = re.compile(r"[A-Za-z0-9_.\-/]+")


def settings() -> dict:
    config = tool_config("gate_wiring")
    return {
        "makefile": str(config.get("makefile", DEFAULT_MAKEFILE)),
        "guards_dir": str(config.get("guards_dir", DEFAULT_GUARDS_DIR)),
        "guards_glob": str(config.get("guards_glob", DEFAULT_GUARDS_GLOB)),
        "roots": [str(name) for name in config.get("roots", DEFAULT_ROOTS)],
        "exempt": {str(name) for name in config.get("exempt", ())},
    }


def misconfigured(message: str) -> None:
    print(f"ОШИБКА НАСТРОЙКИ: {message}")
    sys.exit(2)


def strip_comment(line: str) -> str:
    """Строка без комментария; закомментированная целиком — пустая.

    `#` начинает комментарий только после пробела или в начале строки: иначе
    обрезались бы подстановки вида `$${answer#YES:}`.
    """
    if line.strip().startswith("#"):
        return ""
    found = re.search(r"(?:^|\s)#", line)
    return line[: found.start()] if found else line


class Makefile:
    """Граф целей: зависимости и рецепты, из которых убраны комментарии."""

    def __init__(self, text: str) -> None:
        self.prereqs: dict[str, list[str]] = {}
        self.recipes: dict[str, list[str]] = {}
        self.markers = ["$(MAKE)", "${MAKE}"]
        self._parse(text)

    def _parse(self, text: str) -> None:
        current: list[str] = []
        for raw in text.splitlines():
            if raw.startswith("\t"):
                body = strip_comment(raw)
                for name in current:
                    self.recipes.setdefault(name, []).append(body)
                continue

            assignment = ASSIGNMENT.match(raw)
            if assignment:
                # Переменная, в которую записан сам make: вызов через неё —
                # такой же вложенный вызов, как `$(MAKE)` в строке.
                if "$(MAKE)" in assignment.group(2):
                    self.markers.append(f"$({assignment.group(1)})")
                continue

            target = TARGET.match(raw)
            if not target:
                if raw.strip():
                    current = []
                continue
            current = target.group(1).split()
            for name in current:
                self.prereqs.setdefault(name, []).extend(strip_comment(target.group(2)).split())
                self.recipes.setdefault(name, [])

    def edges(self, target: str) -> set[str]:
        """Куда ведёт цель: зависимости плюс цели, вызванные вложенным make.

        На строке с вложенным вызовом берутся ВСЕ известные цели, а не соседний
        токен: имя там бывает собрано подстановкой. Лишнее ребро делает сторожа
        снисходительнее, пропущенное — заставило бы врать красным.
        """
        found = {name for name in self.prereqs.get(target, []) if name in self.prereqs}
        for line in self.recipes.get(target, []):
            if not any(marker in line for marker in self.markers):
                continue
            found |= {token for token in TOKEN.findall(line) if token in self.prereqs}
        return found

    def reachable(self, roots: list[str]) -> set[str]:
        seen: set[str] = set()
        queue = list(roots)
        while queue:
            target = queue.pop()
            if target in seen:
                continue
            seen.add(target)
            queue.extend(self.edges(target))
        return seen

    def commands(self, targets: set[str]) -> str:
        return "\n".join(line for target in sorted(targets) for line in self.recipes[target])


def report_unwired(missing: list[str], roots: list[str]) -> None:
    listing = "\n".join(f"  {name}" for name in missing)
    print(
        f"ОТКАЗ: {plural(len(missing), 'сторож', 'сторожа', 'сторожей')} не "
        f"{plural(len(missing), 'вызывается', 'вызываются', 'вызываются')} из гейта.\n"
        f"\n{listing}\n"
        "\n"
        f"Цель гейта: {', '.join(f'`make {root}`' for root in roots)}. Файл лежит в\n"
        "репозитории, тесты на него есть, а проверка не выполняется никогда — и\n"
        "выглядит это в точности как пройденная.\n"
        "\n"
        "Что делать:\n"
        "  - дописать вызов в цель, достижимую из гейта (`layout-check`,\n"
        "    `interface-check` и прочие входят в `lint-check`);\n"
        "  - либо, если сторож намеренно живёт вне гейта, назвать его в `exempt`\n"
        "    в `[tool.gate_wiring]` и написать рядом причину.\n"
        f"\nПодробно: {RULE}"
    )


def report_dangling(dangling: list[str]) -> None:
    listing = "\n".join(f"  {name}" for name in dangling)
    print(
        f"ОТКАЗ: гейт зовёт {plural(len(dangling), 'файл', 'файла', 'файлов')}, "
        f"{plural(len(dangling), 'которого', 'которых', 'которых')} нет.\n"
        f"\n{listing}\n"
        "\n"
        "`make` упадёт на «No such file or directory», но не у того, кто\n"
        "переименовал файл, а у следующего, кто запустит прогон.\n"
        f"\nПодробно: {RULE}"
    )


def main() -> int:
    config = settings()

    makefile_path = ROOT / config["makefile"]
    if not makefile_path.is_file():
        misconfigured(
            f"файла `{config['makefile']}` в корне проекта нет, а в нём лежит гейт.\n"
            "  Проверке нечего разбирать — поправь `makefile` в [tool.gate_wiring]."
        )

    guards_dir = require_dir(ROOT / config["guards_dir"], "[tool.gate_wiring].guards_dir")
    guards = sorted(path.name for path in guards_dir.glob(config["guards_glob"]))
    if not guards:
        misconfigured(
            f"в `{config['guards_dir']}` нет ни одного файла по шаблону "
            f"`{config['guards_glob']}`.\n"
            "  Пустой скан — это не «ОК», а промах шаблона или каталога."
        )

    makefile = Makefile(makefile_path.read_text(encoding="utf-8"))
    unknown = [root for root in config["roots"] if root not in makefile.prereqs]
    if unknown:
        misconfigured(
            f"цели {', '.join(f'`{name}`' for name in unknown)} в "
            f"`{config['makefile']}` нет.\n"
            "  Достижимость считать не от чего — поправь `roots` в [tool.gate_wiring]."
        )

    gate = makefile.commands(makefile.reachable(config["roots"]))

    # Имя ищется ОТДЕЛЬНЫМ токеном, а не подстрокой: иначе сторож, чьё имя
    # вложено в имя соседа, зачёлся бы вызванным за компанию с ним.
    called_names = {token.split("/")[-1] for token in TOKEN.findall(gate)}
    missing = [
        name for name in guards if name not in called_names and name not in config["exempt"]
    ]
    if missing:
        report_unwired(missing, config["roots"])
        return 1

    pattern = rf"{re.escape(config['guards_dir'])}/([A-Za-z0-9_.\-]+\.py)"
    called = set(re.findall(pattern, gate))
    dangling = sorted(
        f"{config['guards_dir']}/{name}" for name in called if not (guards_dir / name).is_file()
    )
    if dangling:
        report_dangling(dangling)
        return 1

    wired = len(guards) - len(config["exempt"] & set(guards))
    exempted = f", вне гейта намеренно: {len(config['exempt'])}" if config["exempt"] else ""
    print(
        f"Gate wiring: {plural(wired, 'сторож', 'сторожа', 'сторожей')} "
        f"достижимы из `make {config['roots'][0]}`{exempted}. OK."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
