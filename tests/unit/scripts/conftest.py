"""Сторожа проверяются ЗАПУСКОМ, а не импортом.

Половина из них зовёт `source_root()` прямо на импорте, так что подменять
корень монкипатчем поздно. Каждый тест собирает во временном каталоге
маленький проект и запускает сторожа в нём отдельным процессом — заодно
проверяя тот самый контракт, которым пользуется Makefile: КОД ВОЗВРАТА.

Отсюда главный класс тестов в этой папке: не «ловит ли сторож нарушение», а
«не зеленеет ли он молча» — на пустом каталоге, на опечатке в конфиге, на
псевдониме импорта. Настоящие отказы репозитория были именно такими.
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


def child_env(**extra: str) -> dict[str, str]:
    """Окружение дочернего процесса — БЕЗ переменных coverage.

    По `COV_CORE_*` pytest-cov меряет и потомка, а его `./app` — файлы,
    написанные самим тестом: сторож планки отбивал прогон, где настоящий
    `app/` никто не трогал. Отбрасывается всё на `COV`, терять тут нечего.
    """
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("COV")
    }
    environment.update(extra)
    return environment


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
        # Про отсутствие переменных coverage — в `child_env`.
        environment = child_env(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
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
