"""Агрегатор называет каждого соседа по пакету.

Файл, которого нет в `__init__.py`, не поднимется — и это будет не ошибкой
сборки, а отсутствием ручки в проде. Главный тест здесь — про резолюцию
импорта в ПУТЬ: сверка по последнему сегменту засчитывала соседа упомянутым по
чужому импорту с тем же хвостом.
"""

from tests.unit.scripts.conftest import Repo

PACKAGES = """
[tool.package_coverage]
packages = ["app/interface/api/routes"]
"""


class TestPackageCoverage:
    def test_mentioned_neighbour_passes(self, repo: Repo) -> None:
        repo.pyproject(PACKAGES)
        repo.write(
            "app/interface/api/routes/__init__.py",
            "from app.interface.api.routes.users import router\n",
        )
        repo.write("app/interface/api/routes/users.py", "router = 1\n")

        result = repo.run("check_package_coverage")

        assert result.code == 0

    def test_relative_import_counts(self, repo: Repo) -> None:
        repo.pyproject(PACKAGES)
        repo.write("app/interface/api/routes/__init__.py", "from .users import router\n")
        repo.write("app/interface/api/routes/users.py", "router = 1\n")

        result = repo.run("check_package_coverage")

        assert result.code == 0

    def test_forgotten_neighbour_is_reported(self, repo: Repo) -> None:
        repo.pyproject(PACKAGES)
        repo.write(
            "app/interface/api/routes/__init__.py",
            "from app.interface.api.routes.users import router\n",
        )
        repo.write("app/interface/api/routes/users.py", "router = 1\n")
        repo.write("app/interface/api/routes/jobs.py", "router = 2\n")

        result = repo.run("check_package_coverage")

        assert result.code == 1
        assert result.mentions("не упомянуто — jobs")

    def test_same_tail_from_another_package_does_not_count(self, repo: Repo) -> None:
        """`from ...guards.users import x` — про чужой пакет, хвост тот же.

        Сверка по последнему сегменту засчитывала соседа упомянутым, и живая
        ручка ни разу не включалась в агрегатор.
        """
        repo.pyproject(PACKAGES)
        repo.write(
            "app/interface/api/routes/__init__.py",
            "from app.interface.api.guards.users import require_api_key\n",
        )
        repo.write("app/interface/api/routes/users.py", "router = 1\n")

        result = repo.run("check_package_coverage")

        assert result.code == 1
        assert result.mentions("не упомянуто — users")

    def test_subpackage_without_init_is_still_a_neighbour(self, repo: Repo) -> None:
        """Забыть `__init__.py` — частая случайность, а каталог выпадал дважды:
        и из списка соседей, и из обхода по агрегаторам."""
        repo.pyproject(PACKAGES)
        repo.write("app/interface/api/routes/__init__.py", "")
        repo.write("app/interface/api/routes/admin/panel.py", "router = 1\n")

        result = repo.run("check_package_coverage")

        assert result.code == 1
        assert result.mentions("не упомянуто — admin")

    def test_missing_package_dir_is_a_config_error(self, repo: Repo) -> None:
        repo.pyproject(PACKAGES)

        result = repo.run("check_package_coverage")

        assert result.code == 2
        assert result.mentions("[tool.package_coverage].packages")
