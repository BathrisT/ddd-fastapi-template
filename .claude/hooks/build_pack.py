"""Сборка пакетов для линз: `make review-pack`.

Пакет — это один файл, в котором лежит всё, с чего линза начинает: заголовок с
базой и хэшем, карта изменённого, карта всего репозитория и дальше каждый
изменённый файл. Одно чтение вместо десятка, и — что важнее — хэш в заголовке
считает тот же код, что потом сверяет гейт, поэтому разъехаться они не могут.

Файл показывается СКЕЛЕТОМ: видны все определения, тело развёрнуто только там,
где есть правка или вызов изменённого, остальное свёрнуто в заглушку
`<N строк без правок>` на строке сигнатуры. Номер строки в пакете при этом
остаётся номером строки в файле, поэтому находка `file:line` проверяется прямо
здесь. Раньше файл разворачивался целиком: на диффе в 157 файлов это давало
29 442 строки, то есть пятнадцать чтений по 2000, и линза читала по диагонали
не от лени, а потому что иначе не могла.

Окончания строк не считаются правкой (`--ignore-cr-at-eol`). Файл, переехавший
с LF на CRLF, git показывает переписанным целиком: в том же диффе так выглядели
50 файлов, и тест на 1704 «изменённые» строки содержал две настоящие правки.
`Review.hash` нормализует CRLF по той же причине с самого начала — пакет просто
догнал вердикт.

Пакетов столько, сколько проходов у линзы, и различаются они ПОРЯДКОМ файлов:
проход 2 начинает с середины, проход 3 — с последней трети, дальше по кругу.
Порядок чтения влияет на то, что агент замечает, и без этого три прохода одной
линзы выродились бы в три одинаковых. Проворот, а не случайность: перезапустил
раунд — получил тот же порядок, иначе отладка невозможна.

Заодно скрипт ведёт журналы линз: при смене базы (то есть когда предыдущее
ревью закончилось коммитом) обнуляет их, иначе схлопывает дубли, которые могли
записать параллельные проходы. Линзе поэтому не надо решать, что помнить, а что
забыть, — она только дописывает.
"""

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review import COPIES_PER_LENS, LENS_COUNT, Review  # noqa: E402

# Контекст на весь файл: юнифицированный дифф с таким запасом разворачивается в
# файл целиком, и отдельная выгрузка содержимого не нужна.
_WHOLE_FILE = "1000000"
# Не-python показывается обычным диффом: скелет строится по AST, а для .md и
# .toml его нет. `pyproject.toml` разворачивался в 322 строки ради четырёх правок.
_PLAIN_CONTEXT = "5"
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NOTES_HEADER = "# Журнал линзы {lens}: рассмотрено и отложено\nbase: {base}\n"
_TESTS = "tests/"


class Diff:
    """Дифф в виде строк с номерами. Окончания строк правкой не считаются."""

    @staticmethod
    def run(cwd: str, base: str, *args: str) -> str:
        raw = Review.run_git(cwd, "diff", "--ignore-cr-at-eol", base, *args)
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def files(cwd: str, base: str) -> list[tuple[int, int, str, str]]:
        """`(добавлено, удалено, прежний путь, нынешний путь)`.

        Разбор через `-z`: при переименовании git пишет путь как
        `{a.py => b.py}`, и такой строкой `git show` молча отдаёт пустоту —
        файл посчитался бы удалённым. С `-z` пути приходят двумя отдельными
        полями.
        """
        raw = Diff.run(cwd, base, "--numstat", "-z").split("\0")
        files: list[tuple[int, int, str, str]] = []
        index = 0
        while index < len(raw) and raw[index]:
            parts = raw[index].split("\t")
            if len(parts) < 3:
                index += 1
                continue
            added, deleted, path = parts
            if path == "":
                old, new = raw[index + 1], raw[index + 2]
                index += 3
            else:
                old = new = path
                index += 1
            files.append(
                (
                    int(added) if added.isdigit() else 0,
                    int(deleted) if deleted.isdigit() else 0,
                    old,
                    new,
                )
            )
        return files

    @staticmethod
    def rows(cwd: str, base: str, old: str, new: str) -> list[tuple[str, int | None, str]]:
        """`(пометка, номер строки в нынешнем файле, содержимое)` по всему файлу.

        У удалённой строки номера в нынешнем файле нет — на его месте `None`.
        """
        text = Diff.run(cwd, base, f"-U{_WHOLE_FILE}", "--", old, new)
        rows: list[tuple[str, int | None, str]] = []
        line_no = 0
        in_hunk = False
        for line in text.splitlines():
            if not in_hunk:
                if line.startswith("Binary files"):
                    return [("!", None, "(бинарный файл — содержимое не показано)")]
                match = _HUNK_RE.match(line)
                if match:
                    line_no = int(match.group(1))
                    in_hunk = True
                continue
            if line.startswith("\\"):  # «No newline at end of file»
                continue
            marker, content = line[:1], line[1:].rstrip("\r")
            if marker == " ":
                rows.append((" ", line_no, content))
                line_no += 1
            elif marker == "+":
                rows.append(("+", line_no, content))
                line_no += 1
            elif marker == "-":
                rows.append(("-", None, content))
        return rows

    @staticmethod
    def touched(rows: list[tuple[str, int | None, str]]) -> set[int]:
        """Номера строк, которые считаются правкой.

        Удалённой строки в нынешнем файле нет, и по одному только `+` чистое
        удаление внутри функции было невидимо: тело сворачивалось в заглушку
        `<N строк без правок>` — то есть пакет утверждал, что правки нет, ровно
        там, где сняли проверку или ранний выход. Поэтому удаление получает
        якорь: строки-соседи сверху и снизу. Соседа два, потому что одного
        мало — удаление в начале тела упирается в `def`, в конце — в чужой.
        """
        marked = {no for marker, no, _ in rows if marker == "+" and no}
        previous: int | None = None
        pending = False
        for marker, line_no, _ in rows:
            if marker == "-":
                pending = True
                if previous is not None:
                    marked.add(previous)
                continue
            if line_no is None:
                continue
            if pending:
                marked.add(line_no)
                pending = False
            previous = line_no
        return marked

    @staticmethod
    def line(marker: str, line_no: int | None, content: str, note: str = "") -> str:
        head = "+ " if marker == "+" else ("- " if marker == "-" else "  ")
        return f"{head}{line_no or '':>4} │ {content}{note}"

    @staticmethod
    def squeeze(rows: list[str]) -> list[str]:
        """Серия пустых строк — одной. Номер остаётся у первой.

        Между схлопнутыми классами разделители встают вплотную: две пустые
        строки по PEP 8 плюс две от соседа. В файле они разделяют, в пакете —
        уже нет.
        """
        out: list[str] = []
        blank = False
        for row in rows:
            empty = "│" in row and not row.split("│", 1)[1].strip()
            if empty and blank:
                continue
            out.append(row)
            blank = empty
        return out

    @staticmethod
    def plain(cwd: str, base: str, old: str, new: str) -> list[str]:
        """Обычный дифф с контекстом — для всего, у чего нет AST."""
        text = Diff.run(cwd, base, f"-U{_PLAIN_CONTEXT}", "--", old, new)
        out: list[str] = []
        line_no = 0
        in_hunk = False
        for line in text.splitlines():
            if line.startswith("Binary files"):
                return ["    (бинарный файл — содержимое не показано)"]
            match = _HUNK_RE.match(line)
            if match:
                if in_hunk:
                    out.append("       │ …")
                line_no = int(match.group(1))
                in_hunk = True
                continue
            if not in_hunk or line.startswith("\\"):
                continue
            marker, content = line[:1], line[1:].rstrip("\r")
            if marker == "-":
                out.append(Diff.line("-", None, content))
            elif marker in " +":
                out.append(Diff.line(marker, line_no, content))
                line_no += 1
        return Diff.squeeze(out) or ["    (изменений в содержимом нет — режим или права)"]


class Skeleton:
    """Python-файл в виде скелета: определения видны все, тела — по делу."""

    @staticmethod
    def defs(tree: ast.AST) -> list[tuple[ast.AST, ast.ClassDef | None]]:
        """Все функции с их классом. Класс нужен ключом: по голому имени в
        тестовом файле с десятью фейковыми репозиториями правка одного
        `list_by_plan` разворачивала все шесть."""
        found: list[tuple[ast.AST, ast.ClassDef | None]] = []

        def walk(node: ast.AST, owner: ast.ClassDef | None) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append((child, owner))
                    walk(child, owner)
                elif isinstance(child, ast.ClassDef):
                    walk(child, child)

        walk(tree, None)
        return found

    @staticmethod
    def opens_at(node: ast.AST) -> int:
        decorators = [item.lineno for item in getattr(node, "decorator_list", [])]
        return min(decorators) if decorators else node.lineno

    @staticmethod
    def ends_at(node: ast.AST, lines: list[str]) -> int:
        """Конец определения с хвостовыми комментариями.

        `end_lineno` — конец последнего ОПЕРАТОРА: комментарий после него AST
        не виден, оставался снаружи и печатался как данные модуля. Хвост
        забирается, пока строки отбиты глубже самого определения; пустые в
        конец не входят — они разделители, и место им снаружи.
        """
        end = probe = node.end_lineno
        probe += 1
        while probe <= len(lines):
            text = lines[probe - 1]
            if not text.strip():
                probe += 1
                continue
            indent = len(text) - len(text.lstrip())
            if indent > node.col_offset and text.lstrip().startswith("#"):
                end = probe
                probe += 1
                continue
            break
        return end

    @staticmethod
    def head_end(node: ast.AST, lines: list[str]) -> int:
        """Последняя строка заголовка — та, что с двоеточием.

        Между заголовком и первым оператором бывают только комментарии и
        пустые строки; они принадлежат телу, а не сигнатуре, иначе заглушка
        встаёт ПОСЛЕ комментария и притворяется, что он относится к соседу.
        """
        line = node.body[0].lineno - 1
        while line > node.lineno and (
            not lines[line - 1].strip() or lines[line - 1].lstrip().startswith("#")
        ):
            line -= 1
        return max(line, node.lineno)

    @staticmethod
    def hot(defs: list, changed: set[int]) -> set[int]:
        """`id` функций, которые надо развернуть: изменённые и те, что зовут
        изменённую. Вызов резолвится `self.foo()` в своём классе и `foo()` в
        модуле; `чужой.foo()` пропускается — по нему не сказать, чей это метод."""
        key = {id(fn): (id(owner) if owner else None, fn.name) for fn, owner in defs}
        touched = {
            key[id(fn)]
            for fn, _ in defs
            if set(range(Skeleton.opens_at(fn), fn.end_lineno + 1)) & changed
        }
        own: dict[int | None, dict[str, tuple]] = defaultdict(dict)
        for fn, owner in defs:
            own[id(owner) if owner else None][fn.name] = key[id(fn)]

        expand: set[int] = set()
        for fn, owner in defs:
            if key[id(fn)] in touched:
                expand.add(id(fn))
                continue
            scope = id(owner) if owner else None
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                call = node.func
                if (
                    isinstance(call, ast.Attribute)
                    and isinstance(call.value, ast.Name)
                    and call.value.id == "self"
                ):
                    target = own[scope].get(call.attr)
                elif isinstance(call, ast.Name):
                    target = own[None].get(call.id)
                else:
                    target = None
                if target in touched:
                    expand.add(id(fn))
                    break
        return expand

    @staticmethod
    def fold_classes(
        tree: ast.AST,
        lines: list[str],
        defs: list,
        changed: set[int],
        expand: set[int],
        keep: set[int],
        note: dict[int, str],
    ) -> None:
        """Класс без единой правки — одной строкой заголовка со сводкой.

        Не схлопывается класс, внутри которого метод развёрнут как вызывающий
        изменённое: он развёрнут ровно ради этой связи, и свернуть его значит
        оборвать её.
        """
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            opens, ends = Skeleton.opens_at(node), Skeleton.ends_at(node, lines)
            span = set(range(opens, ends + 1))
            if span & changed:
                continue
            methods = [fn for fn, owner in defs if owner is node]
            if any(id(fn) in expand for fn in methods):
                continue
            header = list(range(opens, Skeleton.head_end(node, lines) + 1))
            keep -= span - set(header)
            for line in span - set(header):
                note.pop(line, None)
            size = ends - opens + 1
            tail = f", {len(methods)} {Skeleton.methods_word(len(methods))}" if methods else ""
            note[header[-1]] = f"  <{size} {Skeleton.plural(size)}{tail}, без правок>"

    @staticmethod
    def methods_word(count: int) -> str:
        tail, hundred = count % 10, count % 100
        if tail == 1 and hundred != 11:
            return "метод"
        if tail in (2, 3, 4) and hundred not in (12, 13, 14):
            return "метода"
        return "методов"

    @staticmethod
    def plural(count: int) -> str:
        tail, hundred = count % 10, count % 100
        if tail == 1 and hundred != 11:
            return "строка"
        if tail in (2, 3, 4) and hundred not in (12, 13, 14):
            return "строки"
        return "строк"

    @staticmethod
    def render(src: str, rows: list, before: str | None) -> list[str]:
        tree = ast.parse(src)
        changed = Diff.touched(rows)
        defs = Skeleton.defs(tree)
        expand = Skeleton.hot(defs, changed)

        imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        import_lines = {i for n in imports for i in range(n.lineno, n.end_lineno + 1)}
        # Держим ВСЁ, кроме импортов и тел функций. Перечислять узлы AST
        # поимённо нельзя: комментарий между членами енума — не узел, и он
        # исчезал молча, хотя объясняет ровно то, ради чего енум заведён.
        # Свёрнутый блок импортов схлопывается ЦЕЛИКОМ, вместе с пустыми
        # строками и комментариями внутри: иначе от него остаётся частокол
        # пустых строк с номерами.
        if import_lines:
            import_lines |= set(range(min(import_lines), max(import_lines) + 1))
        lines = src.splitlines()
        keep = set(range(1, len(lines) + 1)) - import_lines
        for fn, _ in defs:
            keep -= set(range(Skeleton.opens_at(fn), Skeleton.ends_at(fn, lines) + 1))
        note: dict[int, str] = {}
        for fn, _ in defs:
            opens, ends = Skeleton.opens_at(fn), Skeleton.ends_at(fn, lines)
            if id(fn) in expand:
                keep |= set(range(opens, ends + 1))
                continue
            head = Skeleton.head_end(fn, lines)
            keep |= set(range(opens, head + 1))
            hidden = ends - head
            # Пометка в строке заголовка, а не следующей: класс тогда читается
            # оглавлением, и не остаётся строк без номера.
            note[head] = f"  <{hidden} {Skeleton.plural(hidden)} без правок>"
        Skeleton.fold_classes(tree, lines, defs, changed, expand, keep, note)

        # Сводка импортов встаёт на своё место в файле, а не в начало раздела:
        # строка с номером 24 перед строкой 1 читается как сбой порядка.
        summary = Skeleton.imports_line(src, imports, changed, before)
        first_import = min(import_lines, default=0)
        out: list[str] = []
        pending: list[str] = []
        for marker, line_no, content in rows:
            if line_no is None:
                pending.append(content)
                continue
            if line_no in import_lines and line_no not in keep:
                if line_no == first_import:
                    out += summary
                pending.clear()
                continue
            if line_no in keep:
                out += [Diff.line("-", None, gone) for gone in pending]
                out.append(Diff.line(marker, line_no, content, note.get(line_no, "")))
            pending.clear()
        return Diff.squeeze(out + Skeleton.dropped(before, defs))

    @staticmethod
    def import_text(src: str, node: ast.AST) -> str:
        lines = src.splitlines()
        return " ".join(" ".join(lines[node.lineno - 1 : node.end_lineno]).split())

    @staticmethod
    def imports_line(src: str, imports: list, changed: set[int], before: str | None) -> list[str]:
        """Импорты — строкой со счётчиком; пришедшие и ушедшие названы поимённо.

        Ушедшие берутся сравнением с прежним файлом, а не из строк с минусом:
        многострочный импорт приезжает несколькими такими строками, и собрать
        из них имя пакета обратно нельзя.
        """
        if not imports and not before:
            return []
        arrived = [n for n in imports if set(range(n.lineno, n.end_lineno + 1)) & changed]
        gone: list[str] = []
        if before:
            now = {Skeleton.import_text(src, n) for n in imports}
            try:
                past = ast.parse(before)
            except SyntaxError:
                past = None
            if past:
                gone = [
                    text
                    for node in past.body
                    if isinstance(node, (ast.Import, ast.ImportFrom))
                    for text in [Skeleton.import_text(before, node)]
                    if text not in now
                ]
        at = imports[0].lineno if imports else 1
        head = f"  {at:>4} │ импорты: {len(imports)}"
        parts = []
        if arrived:
            parts.append(f"пришло {len(arrived)}")
        if gone:
            parts.append(f"ушло {len(gone)}")
        if not parts:
            return [head]
        out = [head + ", " + ", ".join(parts) + ":"]
        out += [Diff.line("+", None, Skeleton.import_text(src, n)) for n in arrived]
        out += [Diff.line("-", None, text) for text in gone]
        return out

    @staticmethod
    def dropped(before: str | None, defs: list) -> list[str]:
        """Функции, исчезнувшие целиком, — с телом.

        Их удаление скелет иначе не показал бы вовсе: в нынешнем файле такой
        функции нет, а показывать нечего кроме строк с минусом.
        """
        if not before:
            return []
        try:
            past = ast.parse(before)
        except SyntaxError:
            return []
        # Ключ с классом, а не голое имя: `of`, `execute` и `save` в файле
        # повторяются у каждого соседа, и удалённый метод одного класса
        # считался живым, потому что так же зовут метод другого. Сравниваем по
        # ИМЕНИ класса: узлы двух разных разборов не сопоставимы по id
        alive = {(owner.name if owner else None, fn.name) for fn, owner in defs}
        lines = before.splitlines()
        out: list[str] = []
        for fn, owner in Skeleton.defs(past):
            if (owner.name if owner else None, fn.name) in alive:
                continue
            out.append("")
            where = f"{owner.name}.{fn.name}" if owner else fn.name
            out.append(
                Diff.line("-", None, f"УДАЛЕНА {where} (строки {fn.lineno}–{fn.end_lineno})")
            )
            for i in range(Skeleton.opens_at(fn), fn.end_lineno + 1):
                out.append(Diff.line("-", None, lines[i - 1]))
        return out


class Section:
    """Один файл в пакете: либо тело, либо причина, по которой тела нет."""

    @staticmethod
    def of(cwd: str, base: str, old: str, new: str) -> tuple[list[str], str, int, int]:
        """Тело раздела и причина свёртки — заполнено ровно одно из двух."""
        source = Section.read(cwd, "", new)
        if source is None:
            return [], "файл удалён целиком", 0, 0
        before = Section.read(cwd, base, old)
        if not source.strip():
            return [], ("пустой созданный файл" if before is None else "остались только пустые строки"), 0, 0

        rows = Diff.rows(cwd, base, old, new)
        real = [row for row in rows if row[0] in "+-"]
        # Счёт по тому же диффу, что и тело: `numstat` без фильтра объявил бы
        # 299 правок там, где их пять, и линза распределила бы внимание по вранью.
        plus = sum(1 for row in real if row[0] == "+")
        minus = sum(1 for row in real if row[0] == "-")
        if not real:
            return [], ("перенос без правки содержимого" if old != new else "только окончания строк"), 0, 0
        if all(not row[2].strip() for row in real):
            return [], "только пустые строки", 0, 0
        # Тесты вне выборки: их корректность проверяет pytest, а исчезнувшую
        # проверку ловит храповик покрытия. Названы поимённо — пропуск виден.
        if new.startswith(_TESTS):
            return [], "тесты — вне выборки", 0, 0
        if not new.endswith(".py"):
            return Diff.plain(cwd, base, old, new), "", plus, minus

        changed = Diff.touched(rows)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return Diff.plain(cwd, base, old, new), "", plus, minus
        imported = set()
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported |= set(range(node.lineno, node.end_lineno + 1))
        if changed and changed <= imported:
            return [], "изменились только импорты", 0, 0
        return Skeleton.render(source, rows, before), "", plus, minus

    @staticmethod
    def read(cwd: str, rev: str, path: str) -> str | None:
        """Содержимое файла: из коммита при `rev`, иначе из рабочего дерева."""
        if rev:
            try:
                return Review.run_git(cwd, "show", f"{rev}:{path}").decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 — в базе файла не было
                return None
        target = Path(cwd) / path
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8", errors="replace")


class Pack:
    @staticmethod
    def repo_map(cwd: str) -> list[str]:
        """Каталоги репозитория с числом файлов — и НЕ список файлов.

        Зачем вообще: линза читает CLAUDE.md, где написано, как проект
        ЗАДУМАН, а карта показывает, как он устроен на самом деле. Разница
        между обещанным и заведённым — прямой мандат линзы 3.

        Каталоги, а не файлы, — ради стоимости. Список имён растёт вместе с
        проектом; число каталогов растёт кратно медленнее, а структурный вопрос
        «какие роли заведены и какие пустуют» закрывает одинаково.

        Источник — `git ls-files`: в карту физически не может попасть
        неотслеживаемый мусор вроде дампа падения шелла в корне.
        """
        raw = Review.run_git(cwd, "ls-files").decode("utf-8", errors="replace")
        counts: dict[str, int] = defaultdict(int)
        for path in raw.splitlines():
            if not path:
                continue
            counts[path.rsplit("/", 1)[0] if "/" in path else "."] += 1
        if not counts:
            return ["## Карта репозитория", "", "  (пусто)"]

        # Промежуточные каталоги, у которых нет собственных файлов, в
        # `ls-files` не появляются вовсе — а в дереве они нужны узлами, иначе
        # ребёнок повиснет без родителя и вложенность станет враньём.
        nodes = set(counts)
        for name in list(counts):
            parts = name.split("/")
            nodes.update("/".join(parts[:depth]) for depth in range(1, len(parts)))

        ordered = sorted(nodes, key=lambda name: name.split("/"))
        labels = {name: "  " * (name.count("/")) + name.rsplit("/", 1)[-1] for name in ordered}
        width = max(len(label) for label in labels.values())
        out = [
            f"## Карта репозитория — {sum(counts.values())} файлов "
            f"в {len(counts)} каталогах",
            "",
        ]
        # Число — только у каталогов со своими файлами: у чисто структурных
        # узлов ноль ничего не сообщает, а строку зашумляет.
        out += [f"  {labels[name]:<{width}}  {counts.get(name) or ''}".rstrip() for name in ordered]
        return out

    @staticmethod
    def rotate(files: list, copy: int) -> list:
        if not files:
            return files
        shift = (copy - 1) * len(files) // COPIES_PER_LENS
        return files[shift:] + files[:shift]

    @staticmethod
    def collect(cwd: str, base: str) -> tuple[list, list]:
        """Разобрать дифф ОДИН раз на все проходы.

        Проходы различаются только порядком разделов, а разбор стоит трёх
        вызовов git на файл. Раньше он повторялся на каждый пакет: на диффе в
        157 файлов это полторы тысячи вызовов вместо пятисот, и сборка не
        укладывалась в две минуты.
        """
        sections: list[tuple[str, str, int, int, list[str]]] = []
        folded: list[tuple[str, str]] = []
        for _, _, old, new in Diff.files(cwd, base):
            body, why, plus, minus = Section.of(cwd, base, old, new)
            label = f"{old} → {new}" if old != new else new
            if why:
                folded.append((label, why))
            else:
                sections.append((old, new, plus, minus, body))
        return sections, folded

    @staticmethod
    def build(
        claude_dir: Path,
        copy: int,
        base: str,
        digest: str,
        parsed: tuple[list, list],
        repo: list[str],
    ) -> Path:
        sections, folded = parsed
        sections = Pack.rotate(sections, copy)
        added = sum(item[2] for item in sections)
        deleted = sum(item[3] for item in sections)

        out: list[str] = [
            f"base: {base}",
            f"hash: {digest}",
            f"проход: {copy} из {COPIES_PER_LENS}",
            f"файлов: {len(sections)}   строк: +{added} −{deleted}",
            "",
            "Файл показан СКЕЛЕТОМ: видны все определения, тело развёрнуто там, где",
            "есть правка или вызов изменённого. Заглушка <N строк без правок> называет",
            "то, что скрыто; номера строк настоящие, дочитать можно Read по файлу.",
            "Окончания строк правкой не считаются: переезд LF↔CRLF в пакет не попадает.",
            "",
            "Порядок разделов у каждого прохода свой. Читай сверху вниз: то, что",
            "у тебя идёт первым, у соседнего прохода идёт последним.",
            "",
            "## Карта изменённого",
        ]
        for index, (old, new, plus, minus, _) in enumerate(sections, start=1):
            moved = f"   (был {old})" if old != new else ""
            out.append(f"  §{index:<3} +{plus:<5} −{minus:<5} {new}{moved}")
        if folded:
            # Свёрнутое названо поимённо: молча пропущенный файл неотличим от
            # непрочитанного, а это ровно то, чего гейт не должен допускать.
            out += ["", "## Свёрнуто без тела"]
            out += [f"  {path}  —  {why}" for path, why in folded]
        out.append("")
        out.extend(repo)
        out.append("")

        for index, (_, new, plus, minus, body) in enumerate(sections, start=1):
            out.append(f"## §{index}  {new}   +{plus} −{minus}")
            out.append("")
            out.extend(body)
            out.append("")

        # Размер — в шапку, чтобы читающий узнал о нём ДО того, как упрётся:
        # `Read` отдаёт до 2000 строк за раз. Считается по готовому телу.
        out.insert(4, f"строк в пакете: {len(out) + 1}")

        target = Review.pack(claude_dir, copy)
        target.write_text("\n".join(out) + "\n", encoding="utf-8")
        return target

    @staticmethod
    def refresh_notes(claude_dir: Path, lens: int, base: str) -> str:
        """Обнулить журнал при смене базы, иначе схлопнуть дубли.

        Смена базы значит, что прошлое ревью закончилось коммитом: заметки про
        тот дифф к новому отношения не имеют, и таскать их дальше — значит
        через месяц иметь свалку про код, которого нет.
        """
        path = Review.notes(claude_dir, lens)
        header = _NOTES_HEADER.format(lens=lens, base=base)
        if not path.exists():
            path.write_text(header, encoding="utf-8")
            return "заведён"

        lines = path.read_text(encoding="utf-8").splitlines()
        recorded = next((ln[len("base:") :].strip() for ln in lines if ln.startswith("base:")), "")
        if recorded != base:
            path.write_text(header, encoding="utf-8")
            return "обнулён (сменилась база)"

        entries: list[str] = []
        for line in lines:
            if line.startswith("- ") and line not in entries:
                entries.append(line)
        path.write_text(header + "\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")
        return f"записей: {len(entries)}"


class Untracked:
    @staticmethod
    def refuse_if_any(cwd: str) -> None:
        """Неотслеживаемый файл — это дыра в ревью, а не мелочь.

        `git diff` untracked-файлы не показывает, значит в пакет они не попадут
        и ни одна линза их не увидит. Новый модуль на 300 строк уехал бы в
        коммит непрочитанным — ровно тот отказ, ради которого гейт отдельно
        запрещает `git add` и `git commit` одной командой.

        Отказ, а не предупреждение: пакет собирают перед ревью руками, увидеть
        сообщение и поправить стоит секунды, а пропущенное предупреждение стоит
        непрочитанного файла.
        """
        raw = Review.run_git(cwd, "ls-files", "--others", "--exclude-standard")
        files = [line for line in raw.decode("utf-8", errors="replace").splitlines() if line]
        if not files:
            return
        print(
            "ОТКАЗ: есть файлы вне индекса — в дифф они не попадут, и линзы их "
            "не увидят:\n  " + "\n  ".join(files) + "\n\n"
            "Добавь их (`git add ...`) или отправь в .gitignore, потом собери "
            "пакеты заново.",
            file=sys.stderr,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    _cwd = sys.argv[1] if len(sys.argv) > 1 else "."
    _claude = Path(_cwd) / ".claude"
    _claude.mkdir(exist_ok=True)

    Untracked.refuse_if_any(_cwd)
    _base = Review.base(_cwd)
    _digest = Review.hash(_cwd, _base)

    print(f"база: {_base}   хэш: {_digest}")
    _parsed = Pack.collect(_cwd, _base)
    _repo = Pack.repo_map(_cwd)
    for _copy in range(1, COPIES_PER_LENS + 1):
        _path = Pack.build(_claude, _copy, _base, _digest, _parsed, _repo)
        _size = len(_path.read_text(encoding="utf-8").splitlines())
        print(f"  {_path.name}: {_size} строк")
    for _lens in range(1, LENS_COUNT + 1):
        print(f"  {Review.notes(_claude, _lens).name}: {Pack.refresh_notes(_claude, _lens, _base)}")
