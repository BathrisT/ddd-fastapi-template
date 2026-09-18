"""В env-файлах нет комментариев на одной строке со значением.

Где именно кончается значение, решает НЕВИДИМЫЙ пробел. `A=dev # прод` читается
как `dev`, а `A=dev# прод` — как `dev# прод` целиком: убери один пробел при
выравнивании, и значение сменится молча. Обратная сторона дороже: значение, в
котором ` #` законен (пароль `p@ss #1`, адрес с фрагментом), теряет хвост без
единой ошибки — ни красного теста, ни отказа старта, просто другое значение.

Правило белое: комментарий имеет право быть, но своей строкой — над значением.
Так он не влияет на разбор ни у одного читателя файла, а не только у того,
который попался сегодня.

Строки, где `#` идёт вплотную к тексту (`C=abc#def`), не трогаем: комментарием
их не считает никто, и запрет ловил бы обычные значения.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural, tool_config  # noqa: E402

DEFAULT_PATTERNS = (".env", ".env.*", "*.env")
DEFAULT_SKIP_DIRS = (".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache")


class Finding:
    def __init__(self, path: Path, line: int, name: str) -> None:
        self.path = path
        self.line = line
        self.name = name

    def __str__(self) -> str:
        where = self.path.relative_to(ROOT).as_posix()
        return f"{where}:{self.line}: комментарий на одной строке со значением `{self.name}`"


class Line:
    """Разбор одной строки. Значение не печатается и никуда не уходит."""

    @staticmethod
    def comment_at(value: str) -> int:
        """Позиция `#`, который читатель файла сочтёт началом комментария, или -1.

        Кавычки учитываются: `#` внутри них — часть значения, а `#` после
        закрывающей кавычки комментарий и есть.
        """
        quote = ""
        escaped = False
        # Начало значения считается пробелом: `A= # c` — тоже комментарий.
        previous = " "
        for index, char in enumerate(value):
            if escaped:
                escaped = False
            elif quote:
                if char == "\\" and quote == '"':
                    escaped = True
                elif char == quote:
                    quote = ""
            elif char in "\"'":
                quote = char
            elif char == "#" and previous.isspace():
                return index
            previous = char
        return -1

    @staticmethod
    def check(raw: str) -> str | None:
        """Имя переменной, у которой значение кончается комментарием, или `None`."""
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            return None
        name, value = stripped.split("=", 1)
        if Line.comment_at(value) == -1:
            return None
        return name.removeprefix("export ").strip()


class EnvFiles:
    @staticmethod
    def patterns() -> tuple[str, ...]:
        listed = tool_config("env_files").get("patterns")
        return tuple(str(item) for item in listed) if listed else DEFAULT_PATTERNS

    @staticmethod
    def skipped() -> set[str]:
        listed = tool_config("env_files").get("skip_dirs")
        return {str(item) for item in listed} if listed else set(DEFAULT_SKIP_DIRS)

    @staticmethod
    def found() -> list[Path]:
        skip = EnvFiles.skipped()
        paths: set[Path] = set()
        for pattern in EnvFiles.patterns():
            for path in ROOT.rglob(pattern):
                if not path.is_file():
                    continue
                if skip & set(path.relative_to(ROOT).parts[:-1]):
                    continue
                paths.add(path)
        return sorted(paths)


def main() -> int:
    files = EnvFiles.found()
    if not files:
        # Пустой скан — отказ, а не бодрое «OK»: иначе опечатка в шаблоне
        # выключает проверку целиком и никто об этом не узнает.
        print(
            "ОШИБКА НАСТРОЙКИ: ни одного env-файла не нашлось.\n"
            f"  Искали по шаблонам: {', '.join(EnvFiles.patterns())}.\n"
            "  Проверять нечего — поправь [tool.env_files].patterns или заведи .env.example."
        )
        return 2

    findings: list[Finding] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, raw in enumerate(text.splitlines(), start=1):
            name = Line.check(raw)
            if name is not None:
                findings.append(Finding(path, number, name))

    if findings:
        print("Комментарии на одной строке со значением:")
        for finding in findings:
            print(f"  {finding}")
        print(
            "\nПеренеси комментарий на строку ВЫШЕ значения. Где кончается значение,\n"
            "иначе решает пробел перед `#`: убери его при выравнивании — и значение\n"
            "сменится молча, без ошибки."
        )
        return 1

    print(f"Env-файлы: {plural(len(files), 'файл', 'файла', 'файлов')} без инлайн-комментариев. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
