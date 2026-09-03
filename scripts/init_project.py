"""Отвязка склонированного шаблона: `make init NAME=myproject`.

Делает три вещи: подставляет имя в `pyproject.toml` и `README.md`, удаляет
`origin`, ЕСЛИ тот указывает на шаблон (чужой не трогает — значит его уже
переставили), и печатает, что изменилось.

КОММИТ НЕ ДЕЛАЕТСЯ намеренно: человек видит дифф и соглашается с ним, а не
обнаруживает постфактум.

Адрес шаблона не вычисляется из `origin`, а приезжает в `[tool.template].url`:
тот, кто сначала переставил `origin`, записал бы туда свой адрес — и проект
«обновлялся» бы сам из себя, молча и всегда успешно.

Скрипт не читает pyproject через `tomllib` и не импортирует `_project`: он
единственный запускается ДО `make install`, тем питоном, который на машине
оказался. Правило целиком — docs/rules/шаблон-и-обновления.md
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
RULE = "docs/rules/шаблон-и-обновления.md"

# Поверхностная проверка: имя едет в `[tool.poetry].name`, и poetry не примет
# ни пробелов, ни кириллицы. Строгую валидацию оставляем ему — здесь отсекаем
# только очевидно нерабочее, чтобы отказ пришёл сразу, а не через `make install`.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def fail(message: str) -> int:
    print(f"ОТКАЗ: {message}\n\nПодробно: {RULE}")
    return 1


def setting(section: str, key: str) -> str:
    """Строковое значение из секции pyproject — без tomllib, см. модульный docstring."""
    text = PYPROJECT.read_text(encoding="utf-8") if PYPROJECT.is_file() else ""
    body = re.search(rf"^\[{re.escape(section)}\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not body:
        return ""
    value = re.search(rf'^{re.escape(key)}\s*=\s*"([^"]*)"', body.group(1), re.M)
    return value.group(1) if value else ""


def current_name() -> str:
    """Имя проекта. Читаются оба места: PEP 621 и poetry."""
    return setting("project", "name") or setting("tool.poetry", "name")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def same_repo(a: str, b: str) -> bool:
    """Один ли это адрес: SSH- и HTTPS-форму считаем равными.

    Не узнав своего, `drop_template_origin` оставит шаблонный `origin` и
    напечатает «он уже свой»: защиты нет, а отчёт говорит, что есть.
    Нормализатор для сравнения, а не разбор URL.
    """

    def norm(url: str) -> str:
        url = url.strip().rstrip("/")
        url = re.sub(r"^[A-Za-z][A-Za-z0-9+.\-]*://", "", url)  # https://, ssh://, git://
        url = re.sub(r"^[^/@]+@", "", url)  # git@, user@ — после схемы, не до
        url = url.replace(":", "/", 1)  # scp-форма `host:owner/repo`
        if url.lower().endswith(".git"):
            url = url[:-4]
        return url.rstrip("/").lower()

    return bool(a) and norm(a) == norm(b)


def rename_pyproject(old: str, new: str) -> bool:
    """Имя меняется ВНУТРИ своей секции, а не первым совпадением по файлу.

    `name = ...` в pyproject встречается дважды, и по расхождению этих двух
    определяется отвязка. Замена первого совпадения держится на порядке
    секций, которого никто не обещал: переставь их — и `init` затрёт признак.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    target = f'name = "{old}"'
    for section in ("project", "tool.poetry"):
        found = re.search(rf"^\[{re.escape(section)}\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
        if not found or target not in found.group(1):
            continue
        start, end = found.span(1)
        body = found.group(1).replace(target, f'name = "{new}"', 1)
        PYPROJECT.write_text(text[:start] + body + text[end:], encoding="utf-8", newline="")
        return True
    return False


def rename_readme(old: str, new: str) -> bool:
    if not README.is_file():
        return False
    lines = README.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].strip() != f"# {old}":
        return False
    lines[0] = lines[0].replace(f"# {old}", f"# {new}", 1)
    # `newline=""` — иначе на Windows перевод строки при записи станет CRLF,
    # и первый же `git diff` после `make init` покажет файл изменённым целиком
    # вместо одной строки заголовка. Ровно то, что человеку велено просмотреть
    # перед коммитом.
    README.write_text("".join(lines), encoding="utf-8", newline="")
    return True


def drop_template_origin(template_url: str) -> str:
    """Убрать `origin`, если он ведёт в шаблон. Возвращает строку для отчёта."""
    if not template_url:
        return "origin не тронут — адрес шаблона не задан, сверять не с чем"

    current = git("remote", "get-url", "origin")
    if current.returncode != 0:
        return "origin отсутствует — добавишь свой, когда появится репозиторий"

    url = current.stdout.strip()
    if not same_repo(url, template_url):
        return f"origin не тронут — он уже свой ({url})"

    removed = git("remote", "remove", "origin")
    if removed.returncode != 0:
        return f"origin удалить не вышло: {removed.stderr.strip()}"
    return "origin удалён — указывал на шаблон, пушить туда проект незачем"


def main() -> int:
    parser = argparse.ArgumentParser(description="Отвязать склонированный шаблон от шаблона")
    parser.add_argument("--name", default="", help="имя проекта")
    args = parser.parse_args()

    template_name = setting("tool.template", "name").strip()
    template_url = setting("tool.template", "url").strip()

    if not template_name:
        return fail(
            "секции [tool.template] в pyproject.toml нет — этот проект не из шаблона,\n"
            "  отвязывать нечего."
        )

    if (ROOT / ".is-template").is_file():
        return fail(
            "здесь лежит `.is-template` — это сам шаблон, а не проект из него.\n"
            "  Переименовывать шаблон в проект нечего. Если файл остался по ошибке,\n"
            "  удали его и повтори."
        )

    existing = current_name()
    if existing != template_name:
        print(
            f'Проект уже отвязан от шаблона: name = "{existing}".\n'
            "Переименование после старта — руками, чтобы не тронуть то, что за это время\n"
            "успело сослаться на имя.\n"
            f"\nПодробно: {RULE}"
        )
        return 0

    name = args.name.strip()
    if not name:
        return fail("имя проекта не задано.\n\n  make init NAME=<имя проекта>")
    if not NAME_RE.match(name):
        return fail(
            f'имя "{name}" не годится для pyproject: латиница, цифры, `.`, `_`, `-`,\n'
            "  первый символ — буква или цифра."
        )
    if name == template_name:
        return fail("имя проекта совпадает с именем шаблона — тогда отвязки не происходит.")

    done = []
    done.append(
        f'pyproject.toml  name = "{template_name}" -> "{name}"'
        if rename_pyproject(template_name, name)
        else f'pyproject.toml  НЕ ТРОНУТ — строки `name = "{template_name}"` там нет'
    )
    done.append(
        f'README.md       заголовок -> "# {name}"'
        if rename_readme(template_name, name)
        else f"README.md       НЕ ТРОНУТ — первая строка не `# {template_name}`"
    )
    done.append(f"git remote      {drop_template_origin(template_url)}")

    body = "\n".join(f"  {line}" for line in done)
    print(
        f"Проект отвязан от шаблона.\n"
        f"\n{body}\n"
        "\n"
        "Коммит НЕ сделан — посмотри дифф и закоммить сам:\n"
        "  git diff\n"
        '  git add -A && git commit -m "старт из шаблона"\n'
        "\n"
        "Свой origin добавляется, когда появится репозиторий:\n"
        "  git remote add origin <адрес>\n"
        "\n"
        "Дальше обычный старт:\n"
        "  cp .env.example .env   (заполнить APP__FERNET_KEY и пароли)\n"
        "  make install && make infra-up && make migrate\n"
        "\n"
        "Обновления из шаблона потом:\n"
        "  make template-diff     что придёт и во что обойдётся\n"
        "  make template-update   слить в ветку-буфер\n"
        f"\nПодробно: {RULE}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
