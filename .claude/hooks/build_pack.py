"""Сборка пакетов для линз: `make review-pack`.

Пакет — это один файл, в котором лежит всё, с чего линза начинает: заголовок с
базой и хэшем, карта изменённых файлов и дальше каждый файл ЦЕЛИКОМ с
пометками изменённых строк. Одно чтение вместо десятка, и — что важнее — хэш в
заголовке считает тот же код, что потом сверяет гейт, поэтому разъехаться они
не могут.

Файл показывается целиком, а не кусками диффа: правку читают в окружении, и
номер строки в пакете совпадает с номером строки в файле — иначе находка
`file:line` требует счёта от заголовка куска. Разворачивать целиком здесь
дёшево: в этом репозитории файл не длиннее 300 строк по собственному правилу.

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

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review import COPIES_PER_LENS, LENS_COUNT, Review  # noqa: E402

# Контекст на весь файл: юнифицированный дифф с таким запасом разворачивается в
# файл целиком, и отдельная выгрузка содержимого не нужна.
_WHOLE_FILE = "1000000"
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NOTES_HEADER = "# Журнал линзы {lens}: рассмотрено и отложено\nbase: {base}\n"


class Pack:
    @staticmethod
    def render_file(cwd: str, base: str, path: str) -> list[str]:
        """Файл целиком: номер строки по ТЕКУЩЕМУ файлу плюс пометка правки."""
        raw = Review.run_git(cwd, "diff", f"-U{_WHOLE_FILE}", base, "--", path)
        text = raw.decode("utf-8", errors="replace")

        rendered: list[str] = []
        line_no = 0
        in_hunk = False
        for line in text.splitlines():
            if not in_hunk:
                if line.startswith("Binary files"):
                    return ["    (бинарный файл — содержимое не показано)"]
                match = _HUNK_RE.match(line)
                if match:
                    line_no = int(match.group(1))
                    in_hunk = True
                continue
            if line.startswith("\\"):  # «No newline at end of file»
                continue
            marker, content = line[:1], line[1:]
            if marker == " ":
                rendered.append(f"    {line_no:>4} │ {content}")
                line_no += 1
            elif marker == "+":
                rendered.append(f"  + {line_no:>4} │ {content}")
                line_no += 1
            elif marker == "-":
                # У удалённой строки номера в текущем файле нет — и это видно
                rendered.append(f"  -      │ {content}")
        return rendered or ["    (изменений в содержимом нет — режим или права)"]

    @staticmethod
    def repo_map(cwd: str) -> list[str]:
        """Каталоги репозитория с числом файлов — и НЕ список файлов.

        Зачем вообще: линза читает CLAUDE.md, где написано, как проект
        ЗАДУМАН, а карта показывает, как он устроен на самом деле. Разница
        между обещанным и заведённым — прямой мандат линзы 3. Пример из этого
        же репозитория: CLAUDE.md называет три адреса для бизнес-правила
        (метод сущности, `domain/services/`, `domain/catalog/policy.py`), а из
        карты сразу видно, что в `app/domain/` заведены только `models/` и
        `events/` — два адреса из трёх пока обещание, а не факт. Без карты это
        находится, только если догадаться пойти посмотреть.

        Каталоги, а не файлы, — ради стоимости, и это не про наш размер.
        Список имён растёт вместе с проектом: в соседнем боевом проекте в
        одном `app/` 797 python-файлов, то есть под две тысячи строк оглавления
        КАЖДОМУ из девяти проходов каждый раунд. Число каталогов растёт кратно
        медленнее, а структурный вопрос «какие роли заведены и какие пустуют»
        закрывает одинаково. Вопрос «есть ли уже где-то такое же» карта не
        закрывает никак — это Grep по содержимому, и пусть им и остаётся.

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

        # Деревом, а не списком полных путей: общий префикс пишется один раз, а
        # не повторяется в каждой строке. На этом репозитории разница невелика,
        # но глубина и ширина растут вместе с проектом — а платит за неё каждый
        # из девяти проходов каждый раунд. Отступ вдобавок читается как форма,
        # ради которой карта и заводилась.
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
    def rotate(files: list[tuple[int, int, str]], copy: int) -> list[tuple[int, int, str]]:
        if not files:
            return files
        shift = (copy - 1) * len(files) // COPIES_PER_LENS
        return files[shift:] + files[:shift]

    @staticmethod
    def build(cwd: str, claude_dir: Path, copy: int, base: str, digest: str) -> Path:
        files = Pack.rotate(Review.changed_files(cwd, base), copy)
        added = sum(item[0] for item in files)
        deleted = sum(item[1] for item in files)

        out: list[str] = [
            f"base: {base}",
            f"hash: {digest}",
            f"проход: {copy} из {COPIES_PER_LENS}",
            f"файлов: {len(files)}   строк: +{added} −{deleted}",
            "",
            "Порядок разделов у каждого прохода свой. Читай сверху вниз: то, что",
            "у тебя идёт первым, у соседнего прохода идёт последним.",
            "",
            "## Карта изменённого",
        ]
        for index, (plus, minus, path) in enumerate(files, start=1):
            out.append(f"  §{index:<3} +{plus:<5} −{minus:<5} {path}")
        out.append("")
        out.extend(Pack.repo_map(cwd))
        out.append("")

        for index, (plus, minus, path) in enumerate(files, start=1):
            out.append(f"## §{index}  {path}   +{plus} −{minus}")
            out.append("")
            out.extend(Pack.render_file(cwd, base, path))
            out.append("")

        # Размер — в шапку, чтобы читающий узнал о нём ДО того, как упрётся:
        # `Read` отдаёт до 2000 строк за раз, и пакет крупнее приходится
        # дочитывать с offset. Без этой строки проход обнаруживает обрыв уже
        # посреди чтения и решает задачу «сколько я не увидел» вместо ревью.
        # Считается по готовому телу и учитывает саму себя.
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
    for _copy in range(1, COPIES_PER_LENS + 1):
        _path = Pack.build(_cwd, _claude, _copy, _base, _digest)
        _size = len(_path.read_text(encoding="utf-8").splitlines())
        print(f"  {_path.name}: {_size} строк")
    for _lens in range(1, LENS_COUNT + 1):
        print(f"  {Review.notes(_claude, _lens).name}: {Pack.refresh_notes(_claude, _lens, _base)}")
