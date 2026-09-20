"""Дымовой прогон: каждый сторож вообще загружается.

Сторож, падающий на импорте, не проверяет ничего, а в соседнем боевом проекте
такой жил годами: он заодно не входил в гейт. Заодно проверяется контракт, на
который опирается Makefile: есть `main()`, есть докстрока, а импорт не делает
работы — иначе `sys.exit` на нём выглядит как отказ проверки.

Список берётся глобом: новый сторож попадает сюда сам. Регистрация — это то,
о чём забывают.
"""

import subprocess
import sys

import pytest

from tests.unit.scripts.conftest import REAL_SCRIPTS, child_env

GUARD_GLOB = "check_*.py"

PROBE = """
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("guard_under_test", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

assert callable(getattr(module, "main", None)), "нет функции main()"
assert (module.__doc__ or "").strip(), "нет докстроки: непонятно, зачем сторож"
print("LOADED")
"""

GUARDS = sorted(path.name for path in REAL_SCRIPTS.glob(GUARD_GLOB))


def test_guards_are_found() -> None:
    # Пустой список — это не «все прошли», а промах глоба или каталога.
    assert GUARDS


@pytest.mark.parametrize("guard", GUARDS)
def test_guard_loads(guard: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", PROBE, str(REAL_SCRIPTS / guard)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"),
        check=False,
    )
    output = completed.stdout + completed.stderr

    assert "Traceback" not in output, f"{guard} не загружается:\n{output}"
    assert completed.returncode == 0, f"{guard} завершился кодом {completed.returncode}:\n{output}"
    assert "LOADED" in output, f"{guard} не доехал до конца импорта:\n{output}"
