"""Путь, названный в конфиге, обязан существовать.

Исключение, выданное несуществующему файлу, не краснеет: сторож молча не
находит того, что ему разрешили не проверять, — а правило, которое эта запись
ослабляла, не проверяется вовсе.

Буквальный путь и дотированный модуль проекта обязаны быть; промах шаблона —
предупреждение. Ключи таблиц проверяются наравне со значениями. Голое имя
(`migrations`) — только под ключом-путём: от слова его иначе не отличить.

Правило целиком, вместе с причинами: docs/rules/проверяющий-контур.md
"""

import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural, source_root, tool_config  # noqa: E402

RULE = "docs/rules/проверяющий-контур.md"

DEFAULT_FILES = ("pyproject.toml", "tach.toml")
DEFAULT_SUFFIXES = (".py", ".toml", ".ini", ".cfg", ".md", ".yml", ".yaml", ".txt", ".xml")

GLOB_CHARS = "*?["
DOTTED = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
BARE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")

# Голое имя каталога (`migrations`, `app`) от обычного слова (`master`,
# `DishkaRoute`) синтаксисом не отличить — отличает только ключ, под которым
# оно лежит. Поэтому такие значения проверяются лишь под ключами-путями;
# список хвостов короткий и намеренно грубый.
PATH_KEYS = ("path", "paths", "dir", "dirs", "root", "roots", "file", "files", "location")


def settings() -> dict:
    config = tool_config("config_paths")
    return {
        "files": [str(name) for name in config.get("files", DEFAULT_FILES)],
        "suffixes": tuple(config.get("suffixes", DEFAULT_SUFFIXES)),
        "module_roots": [str(name) for name in config.get("module_roots", ["app"])],
        "exempt": [str(key) for key in config.get("exempt", ())],
    }


class Candidates:
    """Строки конфига, похожие на ссылку в дерево проекта."""

    def __init__(self, config: dict) -> None:
        self.suffixes = config["suffixes"]
        self.module_roots = config["module_roots"]
        self.exempt = config["exempt"]

    def exempted(self, key: str) -> bool:
        return any(key == skip or key.startswith(f"{skip}.") for skip in self.exempt)

    def path_of(self, value: str, key: str) -> tuple[str, str] | None:
        """`(путь, форма записи)` или `None`, если строка о другом."""
        text = value.strip()
        if not text or text.startswith(("http://", "https://", "git@")):
            return None
        # Пробел внутри — это проза, а не путь: в конфигах лежат и объяснения,
        # а «`assert` в app/: ...» отличается от пути только этим признаком.
        # Путь с пробелом в имени при этом остаётся непроверенным — цена меньше.
        if any(char.isspace() for char in text):
            return None
        if any(char in text for char in GLOB_CHARS):
            return text.strip("/"), "glob"
        if "/" in text or text.endswith(self.suffixes):
            return text.strip("/"), "literal"
        if DOTTED.match(text) and text.split(".")[0] in self.module_roots:
            return text.replace(".", "/"), "module"
        if key.split(".")[-1].lower().endswith(PATH_KEYS) and BARE.match(text):
            return text, "literal"
        return None

    def walk(self, node: object, key: str = "") -> list[tuple[str, str, str]]:
        """`[(путь, форма, где он назван)]` по всему дереву конфига."""
        found: list[tuple[str, str, str]] = []
        if isinstance(node, dict):
            for name, value in node.items():
                here = f"{key}.{name}" if key else str(name)
                if not self.exempted(here):
                    # Имя ключа проверяется как путь только по своей форме
                    # (`app/logging.py` в `per-file-ignores`). Голое имя ключом
                    # быть путём не может: `files` — это ключ `files`, а не
                    # каталог, поэтому ключ-путь тут не подсказка.
                    found.extend(self.walk(name, ""))
                    found.extend(self.walk(value, here))
        elif isinstance(node, list):
            for value in node:
                found.extend(self.walk(value, key))
        elif isinstance(node, str) and not self.exempted(key):
            found_path = self.path_of(node, key)
            if found_path:
                found.append((*found_path, key or "верхний уровень"))
        return found


class Existence:
    """Существует ли названное. Шаблон и буквальный путь — разные вопросы.

    Оснований два: корень проекта и корень исходников — часть ключей
    отсчитывает путь от второго. Почему не третий список — в правиле.
    """

    BASES = (ROOT, source_root())

    @staticmethod
    def fixed_root(pattern: str) -> str:
        """Неподвижная часть шаблона: `app/**/__init__.py` → `app`."""
        parts: list[str] = []
        for part in Path(pattern).parts:
            if any(char in part for char in GLOB_CHARS):
                break
            parts.append(part)
        return "/".join(parts)

    @staticmethod
    def _exists(path: str, kind: str) -> bool:
        for base in Existence.BASES:
            if (base / path).exists():
                return True
            # Дотированное имя — это и каталог-пакет, и модуль: `app.interface.
            # api.app` живёт файлом `app.py`.
            if kind == "module" and (base / f"{path}.py").exists():
                return True
        return False

    @staticmethod
    def _matches(pattern: str) -> bool:
        for base in Existence.BASES:
            try:
                if next(base.glob(pattern), None):
                    return True
            except (ValueError, OSError):  # pragma: no cover — нечитаемый шаблон
                return True
        return False

    @staticmethod
    def verdict(path: str, kind: str) -> tuple[str, str]:
        """`(«ok» | «нет» | «пусто», пояснение)`."""
        if kind != "glob":
            return ("ok", "") if Existence._exists(path, kind) else ("нет", "такого пути нет")

        # Промах в шаблоне — предупреждение, а не отказ: пустой шаблон бывает
        # законен (`.vscode/**` в списке игнорирования), и отличить его от
        # опечатки может только автор.
        root = Existence.fixed_root(path)
        if root and not Existence._exists(root, "literal"):
            return "пусто", f"каталога `{root}` нет — шаблон не покрывает ничего"
        return ("ok", "") if Existence._matches(path) else ("пусто", "шаблон не совпал ни с чем")


def report(missing: list[tuple[str, str, str]]) -> None:
    rows = "\n".join(f"  {where}\n    `{path}` — {why}" for path, where, why in missing)
    print(
        f"ОТКАЗ: конфиг называет {plural(len(missing), 'путь', 'пути', 'путей')}, "
        f"{plural(len(missing), 'которого', 'которых', 'которых')} нет.\n"
        f"\n{rows}\n"
        "\n"
        "Запись, указывающая в пустоту, не краснеет: сторож молча не находит\n"
        "того, что ему разрешили не проверять, — и правило, которое эта запись\n"
        "ослабляла, не проверяется вовсе.\n"
        "\n"
        "Что делать: поправить путь, убрать запись или, если это чужая сущность\n"
        "вне дерева проекта, назвать ключ в `exempt` в `[tool.config_paths]`.\n"
        f"\nПодробно: {RULE}"
    )


def warn(empty: list[tuple[str, str, str]]) -> None:
    rows = "\n".join(f"  {where}: `{path}`" for path, where, _ in empty)
    print(
        f"\nПредупреждение: {plural(len(empty), 'шаблон', 'шаблона', 'шаблонов')} "
        f"не {plural(len(empty), 'совпал', 'совпали', 'совпали')} ни с чем:\n"
        f"{rows}\n"
        "  Бывает законно (каталог редактора в списке игнорирования), поэтому\n"
        "  это не отказ. Но если запись должна была что-то покрывать — она не\n"
        "  покрывает ничего."
    )


def main() -> int:
    config = settings()
    candidates = Candidates(config)

    missing: list[tuple[str, str, str]] = []
    empty: list[tuple[str, str, str]] = []
    checked = 0
    for name in config["files"]:
        path = ROOT / name
        if not path.is_file():
            print(
                f"ОШИБКА НАСТРОЙКИ: файла `{name}` нет, а он назван в "
                "[tool.config_paths].files.\n"
                "  Проверке нечего разбирать — поправь список или заведи файл."
            )
            return 2
        tree = tomllib.loads(path.read_text(encoding="utf-8"))
        # Ключ в `exempt` пишется без имени файла (`tool.mypy...`), поэтому
        # имя файла приклеивается к месту только в отчёте.
        for reference, kind, where in candidates.walk(tree):
            checked += 1
            verdict, why = Existence.verdict(reference, kind)
            if verdict == "нет":
                missing.append((reference, f"{name}: {where}", why))
            elif verdict == "пусто":
                empty.append((reference, f"{name}: {where}", why))

    if missing:
        report(missing)
        return 1

    print(
        f"Config paths: {plural(checked, 'ссылка', 'ссылки', 'ссылок')} в "
        f"{plural(len(config['files']), 'конфиге', 'конфигах', 'конфигах')} на месте. OK."
    )
    if empty:
        warn(empty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
