"""Сторож, не достижимый из гейта, не выполняется никогда.

Главный тест здесь — `test_commented_out_call_is_rejected`: именно на этом
случае соврал бы греп по Makefile, ради которого сторожа и хотелось написать в
пять строк. Вызов остаётся в тексте файла, а проверки больше нет.

Настоящие сторожа в фикстурный каталог копируются целиком, поэтому глоб в
каждом тесте сужен до `gate_probe_*.py`: проверяем разбор Makefile, а не
раскладку шаблона.
"""

from tests.unit.scripts.conftest import Repo

CONFIG = """
[tool.gate_wiring]
guards_glob = "gate_probe_*.py"
"""


def probe(repo: Repo, name: str = "gate_probe_one.py") -> None:
    repo.write(f"scripts/{name}", "def main() -> int:\n    return 0\n")


def makefile(repo: Repo, body: str) -> None:
    repo.write("Makefile", body)


WIRED = """
precommit: lint-check

lint-check:
\tpython scripts/gate_probe_one.py
"""


class TestGateWiring:
    def test_wired_guard_passes(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        probe(repo)
        makefile(repo, WIRED)

        result = repo.run("check_gate_wiring")

        assert result.code == 0

    def test_commented_out_call_is_rejected(self, repo: Repo) -> None:
        """Вызов есть в файле, проверки нет. Греп показал бы зелёное."""
        repo.pyproject(CONFIG)
        probe(repo)
        makefile(
            repo,
            """
            precommit: lint-check

            lint-check:
            \t# python scripts/gate_probe_one.py
            """,
        )

        result = repo.run("check_gate_wiring")

        assert result.code == 1
        assert result.mentions("gate_probe_one.py")

    def test_call_after_inline_comment_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        probe(repo)
        makefile(
            repo,
            """
            precommit: lint-check

            lint-check:
            \techo ok # python scripts/gate_probe_one.py
            """,
        )

        result = repo.run("check_gate_wiring")

        assert result.code == 1

    def test_guard_nobody_calls_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        probe(repo)
        makefile(repo, "precommit: lint-check\n\nlint-check:\n\techo ok\n")

        result = repo.run("check_gate_wiring")

        assert result.code == 1

    def test_target_outside_the_gate_is_rejected(self, repo: Repo) -> None:
        """Цель есть и вызов в ней есть — но из `precommit` до неё не дойти."""
        repo.pyproject(CONFIG)
        probe(repo)
        makefile(
            repo,
            """
            precommit: lint-check

            lint-check:
            \techo ok

            extra-check:
            \tpython scripts/gate_probe_one.py
            """,
        )

        result = repo.run("check_gate_wiring")

        assert result.code == 1

    def test_nested_make_call_counts_as_reachable(self, repo: Repo) -> None:
        """`$(MAKE)` в рецепте — такое же ребро графа, как зависимость."""
        repo.pyproject(CONFIG)
        probe(repo)
        makefile(
            repo,
            """
            precommit:
            \t$(MAKE) steps

            steps:
            \tpython scripts/gate_probe_one.py
            """,
        )

        result = repo.run("check_gate_wiring")

        assert result.code == 0

    def test_make_through_variable_counts_as_reachable(self, repo: Repo) -> None:
        """В этом проекте вложенный вызов идёт через `$(RECURSE)`."""
        repo.pyproject(CONFIG)
        probe(repo)
        makefile(
            repo,
            """
            RECURSE = $(MAKE)

            precommit:
            \t"$(RECURSE)" steps

            steps:
            \tpython scripts/gate_probe_one.py
            """,
        )

        result = repo.run("check_gate_wiring")

        assert result.code == 0

    def test_exempt_guard_passes(self, repo: Repo) -> None:
        repo.pyproject(
            """
            [tool.gate_wiring]
            guards_glob = "gate_probe_*.py"
            exempt = ["gate_probe_two.py"]
            """
        )
        probe(repo)
        probe(repo, "gate_probe_two.py")
        makefile(repo, WIRED)

        result = repo.run("check_gate_wiring")

        assert result.code == 0

    def test_call_of_missing_file_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        probe(repo)
        makefile(
            repo,
            """
            precommit: lint-check

            lint-check:
            \tpython scripts/gate_probe_one.py
            \tpython scripts/check_departed.py
            """,
        )

        result = repo.run("check_gate_wiring")

        assert result.code == 1
        assert result.mentions("check_departed.py")

    def test_missing_makefile_is_a_setup_error(self, repo: Repo) -> None:
        repo.pyproject(CONFIG)
        probe(repo)

        result = repo.run("check_gate_wiring")

        assert result.code == 2

    def test_unknown_root_target_is_a_setup_error(self, repo: Repo) -> None:
        """Опечатка в `roots` не должна давать бодрое «ОК» на пустом графе."""
        repo.pyproject(
            """
            [tool.gate_wiring]
            guards_glob = "gate_probe_*.py"
            roots = ["precomit"]
            """
        )
        probe(repo)
        makefile(repo, WIRED)

        result = repo.run("check_gate_wiring")

        assert result.code == 2

    def test_glob_matching_nothing_is_a_setup_error(self, repo: Repo) -> None:
        repo.pyproject(
            """
            [tool.gate_wiring]
            guards_glob = "nobody_*.py"
            """
        )
        makefile(repo, WIRED)

        result = repo.run("check_gate_wiring")

        assert result.code == 2
