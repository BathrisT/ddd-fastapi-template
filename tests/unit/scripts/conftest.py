"""Сторожа проверяются ЗАПУСКОМ, а не импортом.

`_project.ROOT` вычисляется от `__file__` (`scripts/../`), а половина сторожей
зовёт `source_root()` прямо на импорте модуля — подменять корень монкипатчем
поздно уже к первой строке. Поэтому каждый тест собирает во временном каталоге
маленький проект (копия настоящего `scripts/`, свой `pyproject.toml`, свой
`app/`) и запускает сторожа в нём отдельным процессом.

Побочная выгода важнее удобства: проверяется тот самый контракт, которым
пользуется `Makefile`, — КОД ВОЗВРАТА и текст отчёта. Сторож, у которого отказ
перестал доезжать до `sys.exit`, здесь красный; проверка через вызов внутренней
функции осталась бы зелёной, потому что список ошибок она бы вернула честно.

Отсюда же главный класс тестов в этой папке: не «ловит ли сторож нарушение»,
а **«не зеленеет ли он молча»** — на пустом каталоге, на опечатке в конфиге, на
псевдониме импорта. Настоящие отказы этого репозитория были именно такими:
проверка не проходила, а выглядела пройденной.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_SCRIPTS = REPO_ROOT / "scripts"

# Минимум: только имя проекта. Секции своих правил каждый тест дописывает сам —
# так в тесте видно, какой именно настройкой он управляет, и заодно проверяются
# умолчания сторожей (`source_root` по умолчанию и есть `app`).
BASE_PYPROJECT = """\
[tool.poetry]
name = "fixture-project"
"""


@dataclass(frozen=True)
class Run:
    """Результат запуска сторожа: код возврата и весь его вывод."""

    code: int
    output: str

    def mentions(self, fragment: str) -> bool:
        return fragment in self.output


class Repo:
    """Временный проект, в котором запускают сторожа."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        # Копия настоящих сторожей: тест обязан проверять тот код, который
        # поедет в проект, а не его пересказ.
        shutil.copytree(
            REAL_SCRIPTS, root / "scripts", ignore=shutil.ignore_patterns("__pycache__")
        )
        self.write("app/__init__.py")
        self.pyproject()

    def write(self, relative: str, text: str = "") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(text).lstrip("\n"), encoding="utf-8")
        return path

    def mkdir(self, relative: str) -> Path:
        path = self.root / relative
        path.mkdir(parents=True, exist_ok=True)
        return path

    def pyproject(self, extra: str = "") -> None:
        self.write("pyproject.toml", BASE_PYPROJECT + dedent(extra))

    def run(self, guard: str) -> Run:
        # PYTHONIOENCODING обязателен: отчёты сторожей на русском, а дочерний
        # процесс на Windows кодирует поток по локали (cp1251) — родитель
        # получил бы мусор вместо текста, по которому тест и судит.
        environment = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(
            [sys.executable, str(self.root / "scripts" / f"{guard}.py")],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            check=False,
        )
        return Run(completed.returncode, completed.stdout + completed.stderr)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "project")
