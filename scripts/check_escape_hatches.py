"""Пометки, глушащие проверки, требуют объяснения рядом с собой.

Обе проверки раньше жили в Makefile конвейерами `rg` — и это была дыра худшего
сорта: ripgrep не предустановлен ни на одной ОС, а при его отсутствии обе
строки завершались НУЛЁМ. Человек видел `rg: command not found` в потоке
вывода и зелёный `make lint-check`: проверка против молчаливого обхода правил
сама молчаливо обходилась. `grep` не спасает — в Windows нет и его.

Правила берутся из `[tool.escape_hatches]`: пометка, требуемое объяснение и
область. Проверяется только `app/` — в тестах дублёры и пометки законны.

Вторая половина — про конфиг, а не про код: код, которым держится ДРУГОЕ
правило, нельзя отключить на весь проект. В соседнем боевом проекте под
`PLC0415` в общем списке пряталось 25 импортов внутри функций, то есть штатный
обход `tach`. Список и причины — `[tool.escape_hatches.protected]`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import pyproject, source_root, tool_config  # noqa: E402

APP_DIR = source_root()

_DEFAULT_RULES: list[dict] = [
    {
        "name": "ruff отключён на весь файл",
        "patterns": ["# ruff: noqa"],
        "allow": "# allow-ruff-noqa:",
        "scope": "file",
        "why": (
            "Отключение файла ЦЕЛИКОМ ruff собственным правилом не ловит: этой же "
            "строкой он и выключается. Голый `# noqa` без кода ловит PGH004, "
            "лишний — RUF100; отдельного грепа на них больше нет."
        ),
    },
    {
        "name": "мок вместо дублёра",
        "patterns": ["MagicMock", "mock.patch", "unittest.mock"],
        "allow": "# allow-mock:",
        "scope": "line",
        "why": (
            "Дублёр порта — обычный класс. Мок принимает и тот вызов, которого в "
            "порту уже нет, и тест остаётся зелёным после переименования метода."
        ),
    },
]


class Rule:
    """Одна пометка-побег: что ищем, чем она оправдывается и в каких границах."""

    def __init__(self, raw: dict) -> None:
        self.name = str(raw.get("name", "пометка"))
        self.patterns = [str(p) for p in raw.get("patterns", [])]
        self.allow = str(raw.get("allow", ""))
        self.scope = str(raw.get("scope", "line"))
        self.why = str(raw.get("why", ""))

    def valid(self) -> bool:
        return bool(self.patterns and self.allow and self.scope in {"file", "line"})

    def hits(self, relative: str, text: str) -> list[str]:
        """Сработавшие пометки без объяснения рядом."""
        found: list[str] = []
        # Область «файл»: объяснение принимается где угодно в файле — пометка
        # выключает его целиком, значит и оправдание относится к нему целиком.
        whole_file_ok = self.scope == "file" and self.allow in text
        for number, line in enumerate(text.splitlines(), 1):
            if not any(pattern in line for pattern in self.patterns):
                continue
            if whole_file_ok or (self.scope == "line" and self.allow in line):
                continue
            where = "в файле" if self.scope == "file" else "в этой строке"
            found.append(
                f"{relative}:{number}: {self.name} — нет `{self.allow} <причина>` {where}."
            )
        return found


def rules() -> list[Rule]:
    configured = tool_config("escape_hatches").get("rules")
    return [Rule(raw) for raw in (configured if configured else _DEFAULT_RULES)]


class Protected:
    """Код, которым держится другое правило, нельзя отключить на весь проект.

    Опасность не в подавлении, а в связке: отключают один код — перестаёт
    работать соседний сторож, и об этом не сообщает никто. Построчная пометка
    сюда не относится, речь о списках, действующих всюду и молча.
    """

    @staticmethod
    def _value(tree: dict, dotted: str) -> object:
        node: object = tree
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    @staticmethod
    def _related(entry: str, code: str) -> bool:
        """Запись накрывает защищённый код (или наоборот).

        Граница по цифре, а не по строке: селектор `A` (flake8-builtins) не
        должен считаться подавлением `ANN401` только потому, что так совпали
        буквы.
        """
        if entry == code:
            return True
        long, short = (entry, code) if len(entry) > len(code) else (code, entry)
        return long.startswith(short) and long[len(short)].isdigit()

    @staticmethod
    def violations(config: dict, tree: dict) -> list[str]:
        codes = {str(code): str(why) for code, why in config.get("codes", {}).items()}
        if not codes:
            return []

        errors: list[str] = []
        for dotted in config.get("lists", []):
            value = Protected._value(tree, str(dotted))
            if not isinstance(value, list):
                continue
            for entry in value:
                hit = next((c for c in codes if Protected._related(str(entry), c)), "")
                if hit:
                    errors.append(
                        f"`{dotted}` глушит `{entry}` на весь проект — а этим кодом "
                        f"держится другое правило: {codes[hit]}."
                    )
        return errors


def main() -> int:
    active = [rule for rule in rules() if rule.valid()]
    if not active:
        print(
            "ОШИБКА НАСТРОЙКИ: в [tool.escape_hatches] нет ни одного пригодного правила.\n"
            "  У правила обязаны быть `patterns`, `allow` и `scope` (`file` или `line`)."
        )
        return 2

    errors = Protected.violations(tool_config("escape_hatches").get("protected", {}), pyproject())
    for path in sorted(APP_DIR.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Про кодировку ругается ruff; сторожу тут сказать нечего.
            continue
        relative = path.relative_to(APP_DIR.parent).as_posix()
        for rule in active:
            errors.extend(rule.hits(relative, text))

    if errors:
        print("Проверки заглушены без объяснения:\n")
        for error in errors:
            print(f"  {error}")
        explanations = {rule.name: rule.why for rule in active if rule.why}
        for name, why in explanations.items():
            print(f"\n{name}: {why}")
        print(f"\nВсего: {len(errors)}")
        return 1

    names = ", ".join(rule.name for rule in active)
    guarded = len(tool_config("escape_hatches").get("protected", {}).get("codes", {}))
    print(f"Заглушки проверок: объяснены все ({names}); защищённых кодов: {guarded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
