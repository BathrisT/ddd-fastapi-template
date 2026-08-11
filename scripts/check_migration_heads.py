"""У цепочки миграций ровно одна голова.

Две головы — это не «странное состояние», это сломанный `alembic upgrade head`:
команда отказывается выбирать за вас и падает с «head revision is ambiguous».
Причём падает она не там, где ошибку внесли, а у следующего, кто поднимает
окружение или катит выкладку.

Берётся это тремя способами, и все три не дают ни красного теста, ни отказа
линтера:

1. **Обновление из шаблона.** Миграция шаблона с вашей не конфликтует — файл
   новый, спорить не с чем, слияние проходит чисто. Ровно поэтому сторож и
   нужен: `git` доволен, `alembic` сломан.
2. **Слияние двух веток разработки**, в каждой из которых завели ревизию от
   одного и того же родителя.
3. **Правка `down_revision` руками** — опечатка в хэше рождает вторую цепочку.

Проверка идёт через сам alembic (`ScriptDirectory`), а не через разбор файлов
глазами: heads считаются с учётом `depends_on`, меток веток и явных слияний,
и повторять эту логику своими силами значит однажды разойтись с настоящей.
Соединения с базой не нужно — `heads` читает только каталог ревизий.

Настоящее слияние ветвей (`alembic merge`) сторож не запрещает: после него
голова снова одна, и он замолкает сам.

Правило — docs/rules/шаблон-и-обновления.md
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, plural  # noqa: E402

RULE = "docs/rules/шаблон-и-обновления.md"

# Имя зашито, а не вынесено в pyproject: его и так знают `alembic` из командной
# строки, `check_schema_consistency.py` и `tests/integration/conftest.py`.
# Ключ в конфиге обещал бы, что имя меняется в одном месте, — а менять пришлось
# бы всё равно во всех, просто теперь ещё и не догадавшись, где искать.
CONFIG_NAME = "alembic.ini"


def config_path() -> Path:
    """Файл конфигурации alembic. Нет файла — ошибка настройки, а не «ОК».

    Тихо пропустить проверку здесь нельзя по той же причине, по которой её
    вообще завели: непройденная проверка выглядит как пройденная.
    """
    path = ROOT / CONFIG_NAME
    if not path.is_file():
        print(
            f"ОШИБКА НАСТРОЙКИ: файла `{CONFIG_NAME}` в корне проекта нет.\n"
            "  Проверке нечего смотреть — заведи файл или поправь CONFIG_NAME в скрипте."
        )
        sys.exit(2)
    return path


def heads() -> tuple:
    """(список голов, объект ScriptDirectory) — или громкий отказ."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(config_path()))

    # Здешний ini разворачивает `script_location` сам, через `%(here)s`, и эта
    # ветка на нём не срабатывает. Она для проекта, который приедет со своим
    # ini: относительный путь alembic считает от ТЕКУЩЕГО каталога, а не от
    # ini, и запуск не из корня показал бы пустой каталог ревизий — то есть
    # ноль голов и бодрое «ОК» вместо проверки.
    location = config.get_main_option("script_location") or ""
    if location and not Path(location).is_absolute() and "%(here)s" not in location:
        config.set_main_option("script_location", str(ROOT / location))

    try:
        scripts = ScriptDirectory.from_config(config)
        return tuple(scripts.get_heads()), scripts
    except Exception as error:  # noqa: BLE001 — любой разбор цепочки, а не только наш случай
        print(
            f"ОТКАЗ: цепочку ревизий не удалось прочитать.\n\n  {type(error).__name__}: {error}\n\n"
            "  Обычно это оборванный `down_revision`: ссылка на ревизию, которой нет.\n"
            f"\nПодробно: {RULE}"
        )
        sys.exit(1)


def listing(scripts: object, found: tuple) -> str:
    """Голова, её файл и описание — колонками, иначе список нечитаем."""
    rows = []
    for revision in found:
        script = scripts.get_revision(revision)
        rows.append((revision, Path(script.path).name if script.path else "?", script.doc))
    width = max(len(row[0]) for row in rows), max(len(row[1]) for row in rows)
    return "\n".join(f"  {rev:<{width[0]}}  {name:<{width[1]}}  «{doc}»" for rev, name, doc in rows)


def main() -> int:
    found, scripts = heads()

    if len(found) < 2:
        # Ноль голов — проект без миграций вовсе. Состояние законное: так
        # выглядит репозиторий сразу после `alembic init`.
        return 0

    rows = listing(scripts, found)
    print(
        f"ОТКАЗ: у цепочки миграций {plural(len(found), 'голова', 'головы', 'голов')}, "
        "а должна быть одна.\n"
        f"\n{rows}\n"
        "\n"
        "`alembic upgrade head` в таком состоянии не работает: команда отказывается\n"
        "выбирать голову за вас. Упадёт это не здесь, а у следующего, кто поднимает\n"
        "окружение или катит выкладку.\n"
        "\n"
        "Откуда обычно берётся:\n"
        "  - обновление из шаблона: его миграция с вашей не конфликтует, файл новый,\n"
        "    слияние проходит чисто — и вторая голова появляется молча;\n"
        "  - слияние двух веток, в каждой из которых завели ревизию от одного родителя.\n"
        "\n"
        "Что делать — решение человека, не агента:\n"
        "  alembic merge -m <описание> " + " ".join(found) + "\n"
        "    ветви сходятся в ревизию-слияние, обе истории сохраняются;\n"
        "  либо переуказать `down_revision` у одной из них, вытянув цепочку в линию —\n"
        "    так делают, когда одна из ревизий ещё никуда не накатана.\n"
        f"\nПодробно: {RULE}"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
