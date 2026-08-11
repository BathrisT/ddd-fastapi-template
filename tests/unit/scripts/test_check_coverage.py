"""Храповик покрытия: скрипт умеет только повышать.

Смысл именно в асимметрии — понижение обязано быть решением человека, видимым
в диффе, а не побочным эффектом зелёного прогона. Поэтому тесты проверяют обе
стороны храповика и оба отказа настройки: нет данных прогона и мусор в файле.
"""

import subprocess
import sys

from tests.unit.scripts.conftest import Repo

BASELINE = ".coverage-baseline"


def measure(repo: Repo) -> None:
    """Настоящие данные покрытия: один модуль исполнен, второй нет.

    Проценты подобраны так, чтобы не зависеть от точного числа: важно лишь,
    что итог строго между 0 и 100.
    """
    repo.write(
        "app/measured.py",
        """
        class Measured:
            def run(self) -> int:
                return 1
        """,
    )
    repo.write(
        "app/never_touched.py",
        """
        class Never:
            def run(self) -> int:
                value = 2
                return value
        """,
    )
    repo.write("run_it.py", "from app.measured import Measured\n\nMeasured().run()\n")
    subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--source=app", "run_it.py"],
        cwd=repo.root,
        capture_output=True,
        check=True,
    )


class TestCoverageRatchet:
    def test_first_run_creates_the_baseline(self, repo: Repo) -> None:
        measure(repo)

        result = repo.run("check_coverage")

        assert result.code == 0
        assert (repo.root / BASELINE).is_file()
        assert result.mentions("Закоммить его")

    def test_drop_below_the_baseline_is_rejected(self, repo: Repo) -> None:
        measure(repo)
        repo.write(BASELINE, "99.99\n")

        result = repo.run("check_coverage")

        assert result.code == 1
        assert result.mentions("покрытие просело")

    def test_growth_raises_the_baseline(self, repo: Repo) -> None:
        measure(repo)
        repo.write(BASELINE, "1.00\n")

        result = repo.run("check_coverage")

        assert result.code == 0
        assert float((repo.root / BASELINE).read_text(encoding="utf-8").splitlines()[-1]) > 1.0

    def test_comments_in_the_baseline_are_skipped(self, repo: Repo) -> None:
        measure(repo)
        repo.write(BASELINE, "# достигнутое покрытие\n99.99\n")

        result = repo.run("check_coverage")

        assert result.code == 1

    def test_missing_coverage_data_is_an_error(self, repo: Repo) -> None:
        """Скрипт зовут ПОСЛЕ тестов: пустые данные значат, что прогон не состоялся.

        Ноль в этом месте читался бы как «покрытие обвалилось».
        """
        result = repo.run("check_coverage")

        assert result.code == 2
        assert result.mentions("прогон с покрытием не делался")

    def test_garbage_in_the_baseline_is_an_error(self, repo: Repo) -> None:
        measure(repo)
        repo.write(BASELINE, "почти сто\n")

        result = repo.run("check_coverage")

        assert result.code == 2
