"""Guard against prose crowding out the code it explains.

Комментарий по инерции на каждый чих даёт три беды разом: файл перестаёт
читаться, объяснение расходится с кодом молча, а контекст агента выгорает на
тексте, который агент сам себе и написал — в `app/` проза занимала 72%
токенов. Правило целиком — docs/rules/бюджет-комментариев.md; пороги —
`[tool.comment_budget]`.
"""

import ast
import io
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural, require_dir, source_root, tool_config  # noqa: E402

_DEFAULT_ROOTS = ["app", "tests", "scripts"]
_DEFAULT_MAX_RUN = 4
_DEFAULT_MAX_HEADER = 15
_DEFAULT_MAX_RATIO = 0.4
_DEFAULT_MIN_CODE = 20
_DEFAULT_FREE_LINES = 12


class Budget:
    """Пороги из `[tool.comment_budget]`."""

    def __init__(self) -> None:
        config = tool_config("comment_budget")
        self.max_run = int(config.get("max_run", _DEFAULT_MAX_RUN))
        self.max_header = int(config.get("max_module_docstring", _DEFAULT_MAX_HEADER))
        self.max_ratio = float(config.get("max_ratio", _DEFAULT_MAX_RATIO))
        self.min_code = int(config.get("min_code_lines", _DEFAULT_MIN_CODE))
        self.free_lines = int(config.get("free_prose_lines", _DEFAULT_FREE_LINES))
        self.roots = [str(entry) for entry in config.get("roots", _DEFAULT_ROOTS)]

    def paths(self) -> list[Path]:
        found = [
            require_dir(ROOT / entry, f"[tool.comment_budget].roots = {entry!r}")
            for entry in self.roots
        ]
        return found or [source_root()]


class FileProse:
    """Разметка одного файла: где проза, где код, где шапка модуля."""

    def __init__(self, path: Path) -> None:
        lines = path.read_text(encoding="utf-8").splitlines()
        source = "\n".join(lines)
        self.total = len(lines)
        raw_header, raw_docstrings = self._docstrings(source)
        # Строка из одних кавычек и пустая строка внутри докстроки — синтаксис,
        # а не текст: считать их значило бы запретить обычную форму докстроки
        # «итог, пустая строка, абзац» уже на трёх строках смысла.
        self.header = {number for number in raw_header if self._is_text(lines[number - 1])}
        docstrings = {number for number in raw_docstrings if self._is_text(lines[number - 1])}
        self.prose = self._comments(source) | docstrings | self.header
        # Кавычки кодом тоже не считаются: иначе докстрока подрабатывала бы
        # знаменателем и файл получал бюджет за собственную прозу.
        self.blank = {
            number
            for number, text in enumerate(lines, start=1)
            if not text.strip() and number not in self.prose
        } | ((raw_header | raw_docstrings) - self.prose)

    @staticmethod
    def _is_text(line: str) -> bool:
        return bool(line.strip().strip("\"'").strip())

    @staticmethod
    def _comments(source: str) -> set[int]:
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except (SyntaxError, tokenize.TokenError):
            return set()
        return {token.start[0] for token in tokens if token.type == tokenize.COMMENT}

    @staticmethod
    def _docstrings(source: str) -> tuple[set[int], set[int]]:
        """Шапка модуля отдельно от остальных докстрок.

        Проза — строка-ВЫРАЖЕНИЕ: SQL в переменной это код.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set(), set()

        def is_prose(node: ast.AST) -> bool:
            return (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )

        def span(node: ast.Expr) -> set[int]:
            return set(range(node.lineno, (node.end_lineno or node.lineno) + 1))

        first = tree.body[0] if tree.body else None
        header = span(first) if isinstance(first, ast.Expr) and is_prose(first) else set()
        rest: set[int] = set()
        for node in ast.walk(tree):
            if is_prose(node) and isinstance(node, ast.Expr):
                rest |= span(node)
        return header, rest - header

    def code_lines(self) -> int:
        return self.total - len(self.prose) - len(self.blank)

    def body_runs(self) -> list[tuple[int, int]]:
        """Группы прозы В ТЕЛЕ: (первая строка, сколько прозы).

        Пустая строка группу не разрывает: иначе лимит обходится пробелом.
        """
        body = self.prose - self.header
        runs: list[tuple[int, int]] = []
        start: int | None = None
        count = 0
        for number in range(1, self.total + 2):
            if number in body:
                start = number if start is None else start
                count += 1
            elif number in self.blank and start is not None:
                continue
            elif start is not None:
                runs.append((start, count))
                start, count = None, 0
        return runs


class CommentBudget:
    """Три лимита на один файл."""

    def __init__(self) -> None:
        self.budget = Budget()

    def _file_errors(self, path: Path, relative: str) -> list[str]:
        prose = FileProse(path)
        errors: list[str] = []

        if len(prose.header) > self.budget.max_header:
            errors.append(
                f"{relative}:1: шапка модуля {len(prose.header)} строк при бюджете "
                f"{self.budget.max_header} — развёрнутому рассуждению место в docs/rules/"
            )

        errors.extend(
            f"{relative}:{start}: {plural(count, 'строка', 'строки', 'строк')} прозы подряд "
            f"при лимите {self.budget.max_run}"
            for start, count in prose.body_runs()
            if count > self.budget.max_run
        )

        code = prose.code_lines()
        allowance = max(self.budget.free_lines, round(code * self.budget.max_ratio))
        if code >= self.budget.min_code and len(prose.prose) > allowance:
            errors.append(
                f"{relative}: прозы {len(prose.prose)} строк на "
                f"{plural(code, 'строку', 'строки', 'строк')} кода "
                f"(бюджет {allowance})"
            )
        return errors

    def run(self) -> list[str]:
        errors: list[str] = []
        seen: set[Path] = set()
        for root in self.budget.paths():
            for path in sorted(root.rglob("*.py")):
                if "__pycache__" in path.parts or path in seen:
                    continue
                seen.add(path)
                errors.extend(self._file_errors(path, path.relative_to(ROOT).as_posix()))
        return errors


def main() -> int:
    errors = CommentBudget().run()
    if errors:
        for error in errors:
            print(error)
        print(f"\n{plural(len(errors), 'превышение', 'превышения', 'превышений')} бюджета прозы.")
        print("Комментарий объясняет решение, невидимое в коде, — а не пересказывает код.")
        print("Длинное рассуждение уезжает в docs/rules/, короткое — в шапку модуля.")
        return 1
    print("Comment budget: проза не вытесняет код. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
