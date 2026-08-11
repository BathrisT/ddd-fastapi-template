"""Обновления из шаблона: `--diff`, `--update`, `--graft`.

Адрес шаблона и ветка лежат в `[tool.template]` в pyproject.toml. **Git-remote
для шаблона не заводится:** `git fetch <url> <ref>` работает по адресу напрямую,
складывая результат в `FETCH_HEAD`, а отдельный remote был бы вторым местом
хранения того же адреса — и однажды они разошлись бы молча.

`--diff` не меняет ничего. Кроме списка входящих коммитов он показывает
**предсказание конфликтов**: `git merge-tree` проигрывает слияние в памяти и
называет файлы поимённо, не трогая рабочее дерево. То есть цена обновления
известна до того, как за него взялись.

`--update` сливает в ветку-буфер, а не в рабочую: не понравилось — ветку
удалили, и основная ничего не заметила.

`--graft` — разовая операция для проекта, который начинался не из шаблона:
слияние `-s ours` записывает шаблон родителем, не меняя дерево ни на байт.

Правило целиком — docs/rules/шаблон-и-обновления.md
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural, tool_config  # noqa: E402

RULE = "docs/rules/шаблон-и-обновления.md"
BUFFER_BRANCH = "template-update"

# Строка вида `100644 <oid> 2\tпуть` в выводе `git merge-tree`.
STAGE_RE = re.compile(r"^\d{6} [0-9a-f]+ [123]\t(.+)$")

# Требуется для `--write-tree` у merge-tree.
MIN_GIT = (2, 38)


def fail(message: str) -> int:
    print(f"ОТКАЗ: {message}\n\nПодробно: {RULE}")
    return 1


def git(*args: str) -> subprocess.CompletedProcess:
    """Без `core.quotepath` кириллические пути приезжают экранированными."""
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def out(*args: str) -> str:
    return git(*args).stdout.strip()


def raw(*args: str) -> str:
    """То же, но без съедания отступов: `--stat` рисует колонки пробелами."""
    return git(*args).stdout.rstrip("\n")


def git_version() -> tuple:
    match = re.search(r"(\d+)\.(\d+)", out("--version"))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def under(path: str, prefixes: list) -> bool:
    posix = path.replace("\\", "/")
    return any(posix == p or posix.startswith(f"{p}/") for p in prefixes)


class Template:
    """Адрес шаблона, ветка и пути, которые нельзя разбирать механически."""

    def __init__(self) -> None:
        section = tool_config("template")
        self.url = str(section.get("url", "")).strip()
        self.ref = str(section.get("ref", "master")).strip() or "master"
        self.manual = [str(p).strip("/") for p in section.get("manual", [])]
        self.alert = [str(p).strip("/") for p in section.get("alert", [])]

    def missing_url(self) -> str:
        if self.url:
            return ""
        return (
            "адрес шаблона не задан.\n"
            "  Заполни `url` в секции [tool.template] в pyproject.toml."
        )

    def warn_for(self, path: str) -> str:
        """Почему этот конфликт нельзя разрешать самому."""
        return "НЕ РАЗРЕШАЙ САМ" if under(path, self.manual) else "разбирается по месту"

    def alerting(self, paths: list) -> list:
        """Входящие файлы, опасные САМИМ фактом появления, без всякого конфликта.

        Миграция из шаблона сольётся молча и чисто — она новая, спорить не с
        чем. Но цепочка ревизий у шаблона своя, и после слияния голов в ней
        станет две, а `alembic upgrade head` перестанет работать. Отчёт,
        сказавший «конфликтов не будет», был бы формально прав и практически
        вреден.
        """
        return [p for p in paths if under(p, self.alert)]

    def fetch(self) -> str:
        print(f"Шаблон: {self.url}@{self.ref}")
        result = git("fetch", "--quiet", self.url, self.ref)
        if result.returncode != 0:
            return f"не удалось забрать шаблон:\n{result.stderr.strip()}"
        return ""


class Report:
    """Печать блоков отчёта — чтобы формат был один на все три режима."""

    @staticmethod
    def block(title: str, body: str) -> None:
        line = "─" * max(4, 60 - len(title))
        print(f"\n── {title} {line}")
        if not body.strip():
            print("  (пусто)")
            return
        print("\n".join(f"  {row}".rstrip() for row in body.splitlines()))

    @staticmethod
    def files(paths: list, template: Template) -> str:
        if not paths:
            return ""
        width = max(len(p) for p in paths)
        return "\n".join(f"{p:<{width}}  {template.warn_for(p)}" for p in paths)


class Conflicts:
    """Предсказание конфликтов слияния без изменения рабочего дерева."""

    @staticmethod
    def predict(head: str = "HEAD", other: str = "FETCH_HEAD") -> tuple:
        """(доступно ли предсказание, список путей)."""
        if git_version() < MIN_GIT:
            return False, []
        result = git("merge-tree", "--write-tree", head, other)
        if result.returncode == 0:
            return True, []
        if result.returncode != 1:
            return False, []
        seen = []
        for line in result.stdout.splitlines():
            match = STAGE_RE.match(line)
            if match and match.group(1) not in seen:
                seen.append(match.group(1))
        return True, seen


class Sync:
    def __init__(self, template: Template) -> None:
        self.template = template

    def incoming(self) -> list:
        log = out("log", "--oneline", "--no-decorate", "HEAD..FETCH_HEAD")
        return log.splitlines() if log else []

    def dirty(self) -> bool:
        """Незакоммиченные правки ОТСЛЕЖИВАЕМЫХ файлов.

        `--untracked-files=no` намеренно: теряется в слиянии не всякий лишний
        файл, а несохранённая работа. Считать помехой любой untracked — значит
        отказывать из-за `__pycache__`, который создаёт сам же запуск сторожа,
        а в проекте без нужной строки в `.gitignore` не дать обновиться вовсе.
        Случай, когда слияние собирается затереть неотслеживаемый файл, ловит
        сам git и говорит об этом прямо — этот отказ пересказан ниже.
        """
        return bool(out("status", "--porcelain", "--untracked-files=no"))

    def report_alerting(self, paths: list) -> None:
        """Файлы, опасные фактом появления. Молчит, если таких нет."""
        if not paths:
            return
        Report.block(
            "Требует человека даже без конфликта",
            "\n".join(paths)
            + "\n\nКонфликта они не дали — файлы новые, спорить не с чем. Но цепочка\n"
            "ревизий у шаблона своя: голов теперь две, и `alembic upgrade head`\n"
            "не работает. Перенумеровать и переуказать `down_revision` — работа\n"
            "человека, не агента.",
        )

    def diff(self) -> int:
        commits = self.incoming()
        if not commits:
            print("\nШаблон не ушёл вперёд — брать нечего.")
            return 0

        ours, theirs = (out("rev-list", "--left-right", "--count", "HEAD...FETCH_HEAD") or "0\t0").split()
        incoming = out("diff", "--name-only", "HEAD...FETCH_HEAD").splitlines()

        Report.block(f"Придёт ({plural(len(commits), 'коммит', 'коммита', 'коммитов')})", "\n".join(commits))
        Report.block("Файлы", raw("diff", "--stat", "HEAD...FETCH_HEAD"))
        Report.block("Разошлись", f"ваших коммитов: {ours}\nшаблонных: {theirs}")

        available, paths = Conflicts.predict()
        if not available:
            body = f"предсказание недоступно — нужен git {MIN_GIT[0]}.{MIN_GIT[1]}+"
        elif not paths:
            body = "их не будет — слияние пройдёт само"
        else:
            body = Report.files(paths, self.template)
        Report.block("Конфликты при слиянии", body)

        alerting = self.template.alerting(incoming)
        if alerting:
            Report.block(
                "Требует человека даже без конфликта",
                "\n".join(alerting)
                + "\n\nЭти файлы сольются чисто — они новые, спорить не с чем. Но цепочка\n"
                "ревизий у шаблона своя: после слияния голов станет две, и\n"
                "`alembic upgrade head` перестанет работать. Перенумеровать и\n"
                "переуказать `down_revision` — работа человека, не агента.",
            )

        print(
            "\nНичего не изменено — это только просмотр.\n"
            "Забрать: make template-update\n"
            f"Подробно: {RULE}"
        )
        return 0

    def update(self) -> int:
        if self.dirty():
            return fail(
                "в рабочем дереве есть незакоммиченное.\n"
                "  Разбирать конфликты поверх собственных недоделок — верный способ\n"
                "  потерять и то и другое. Закоммить или спрячь: git stash"
            )
        if not self.incoming():
            print("\nШаблон не ушёл вперёд — брать нечего.")
            return 0
        if git("rev-parse", "--verify", "--quiet", BUFFER_BRANCH).returncode == 0:
            return fail(
                f"ветка `{BUFFER_BRANCH}` уже есть — прошлое обновление не дожато.\n"
                f"  Доделать его или удалить: git branch -D {BUFFER_BRANCH}"
            )

        alerting = self.template.alerting(out("diff", "--name-only", "HEAD...FETCH_HEAD").splitlines())
        base = out("rev-parse", "--abbrev-ref", "HEAD")
        created = git("switch", "-c", BUFFER_BRANCH)
        if created.returncode != 0:
            return fail(f"не удалось завести ветку-буфер:\n{created.stderr.strip()}")

        merged = git("merge", "--no-edit", "-m", f"обновление из шаблона @{self.template.ref}", "FETCH_HEAD")
        if merged.returncode == 0:
            Report.block("Слито без конфликтов", raw("diff", "--stat", f"{base}..HEAD"))
            self.report_alerting(alerting)
            if alerting:
                print(
                    "\nКод возврата ненулевой намеренно: слияние прошло, но пускать его\n"
                    "дальше, пока это не разобрано, нельзя."
                )
            print(
                f"\nЧто дальше:\n"
                f"  make check                      убедиться, что зелено\n"
                f"  git switch {base} && git merge {BUFFER_BRANCH}\n"
                f"\nПередумал: git switch {base} && git branch -D {BUFFER_BRANCH}\n"
                f"Подробно: {RULE}"
            )
            return 1 if alerting else 0

        paths = out("diff", "--name-only", "--diff-filter=U").splitlines()
        if not paths:
            # Слияние сорвалось не на содержимом: обычно оно собиралось затереть
            # неотслеживаемый файл. Своими словами это не пересказать точнее git.
            git("switch", "-q", base)
            git("branch", "-q", "-D", BUFFER_BRANCH)
            return fail(
                "слияние не начато, конфликтов при этом нет. Git говорит так:\n\n"
                + (merged.stderr.strip() or merged.stdout.strip())
                + f"\n\n  Ветка-буфер удалена, дерево осталось на `{base}` нетронутым."
            )
        Report.block(f"Конфликты ({plural(len(paths), 'файл', 'файла', 'файлов')})", Report.files(paths, self.template))
        self.report_alerting(alerting)
        if any(under(p, self.template.manual) for p in paths):
            print(
                "\n«НЕ РАЗРЕШАЙ САМ» — вынеси человеку, а не выбирай сторону:\n"
                "  миграции  — две цепочки ревизий дают две головы, и `alembic upgrade head`\n"
                "              перестаёт работать; какая ревизия за какой, решает человек\n"
                "  исходники — совпадение пути не значит, что шаблонная версия лучше вашей"
            )
        print(
            "\nЧто дальше:\n"
            "  разобрать -> git add <файлы> -> git commit\n"
            "  make check\n"
            f"  git switch {base} && git merge {BUFFER_BRANCH}\n"
            f"\nПередумал: git merge --abort && git switch {base} && git branch -D {BUFFER_BRANCH}\n"
            f"Подробно: {RULE}"
        )
        return 1

    def graft(self) -> int:
        if git("merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD").returncode == 0:
            print("\nШаблон уже привит — его история часть вашей. Обновляйся: make template-diff")
            return 0
        if self.dirty():
            return fail("в рабочем дереве есть незакоммиченное. Закоммить перед прививкой.")

        merged = git(
            "merge",
            "-s",
            "ours",
            "--allow-unrelated-histories",
            "--no-edit",
            "-m",
            f"прививка шаблона {self.template.url}@{self.template.ref}",
            "FETCH_HEAD",
        )
        if merged.returncode != 0:
            return fail(f"прививка не прошла:\n{merged.stderr.strip()}")

        print(
            "\nШаблон привит. Дерево не изменилось ни на байт — изменилось только то,\n"
            "от какой точки git считает дельту.\n"
            "\nЧто дальше:\n"
            "  make template-diff   посмотреть, что шаблон принесёт первым же обновлением\n"
            f"\nПодробно: {RULE}"
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Обновления из шаблона")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--diff", action="store_true", help="что придёт и во что обойдётся")
    mode.add_argument("--update", action="store_true", help="слить в ветку-буфер")
    mode.add_argument("--graft", action="store_true", help="привить шаблон к чужой истории")
    args = parser.parse_args()

    template = Template()
    problem = template.missing_url()
    if problem:
        return fail(problem)

    problem = template.fetch()
    if problem:
        return fail(problem)

    sync = Sync(template)
    if args.diff:
        return sync.diff()
    if args.update:
        return sync.update()
    return sync.graft()


if __name__ == "__main__":
    sys.exit(main())
