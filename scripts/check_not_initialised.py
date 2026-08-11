"""Проект, склонированный из шаблона и не отвязанный от него.

Отвязка (`make init`) — единственное обязательное действие после клонирования,
и единственное, которое нечем напомнить: README можно не прочитать, а красную
проверку — нет. Поэтому сторож живёт в `lint-check`, то есть срабатывает и в
`make check`, и в `make precommit`, и упирается в него агент на первом же
прогоне.

**Отличить шаблон от его свежего клона по содержимому невозможно** — деревья
байт в байт одинаковы, и `origin` у них один и тот же. Значит признак «я и есть
шаблон» обязан жить вне git: локальный `.is-template`, заведённый руками один
раз на машине, где шаблон дорабатывают. Закоммиченный маркер тут бесполезен —
он приедет в клон вместе со всем остальным и будет молчать ровно там, где нужен
голос.

Признак отвязки — расхождение `[tool.poetry].name` с `[tool.template].name`.
Второе поле `make init` не трогает: оно называет шаблон, а не проект.

Правило целиком — docs/rules/шаблон-и-обновления.md
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT, project_name, tool_config  # noqa: E402

MARKER = ROOT / ".is-template"
RULE = "docs/rules/шаблон-и-обновления.md"


def main() -> int:
    template_name = str(tool_config("template").get("name", "")).strip()
    if not template_name:
        # Секции нет — проект не из шаблона, отвязывать не от чего.
        return 0

    if project_name() != template_name:
        return 0

    if MARKER.is_file():
        # Здесь дорабатывают сам шаблон.
        return 0

    print(
        "ОТКАЗ: проект не отвязан от шаблона.\n"
        "\n"
        f'  pyproject.toml   name = "{template_name}" — это имя шаблона, а не проекта\n'
        "\n"
        "Что сделать:\n"
        "  make init NAME=<имя проекта>\n"
        "\n"
        "Дорабатываешь сам шаблон? Заведи локальный признак:\n"
        "  touch .is-template\n"
        "  Файл в .gitignore, в проекты не уезжает, нужен один раз на машину.\n"
        "\n"
        f"Подробно: {RULE}"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
