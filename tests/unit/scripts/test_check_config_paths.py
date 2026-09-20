"""Путь, названный в конфиге, обязан существовать.

Главное здесь — разница в реакции. Буквальный путь в пустоту делает запись
недействующей молча, поэтому отказ. Шаблон, не совпавший ни с чем, бывает
законен (каталог редактора в списке игнорирования), поэтому предупреждение: и
то, и другое печатается, но прогон останавливает только первое.
"""

from tests.unit.scripts.conftest import Repo

BASE = """
[tool.config_paths]
files = ["pyproject.toml"]
"""


class TestConfigPaths:
    def test_existing_path_passes(self, repo: Repo) -> None:
        repo.write("app/config.py")
        repo.pyproject(BASE + '\n[tool.example]\nwhere = "app/config.py"\n')

        result = repo.run("check_config_paths")

        assert result.code == 0

    def test_missing_path_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(BASE + '\n[tool.example]\nwhere = "app/composition/providers/process.py"\n')

        result = repo.run("check_config_paths")

        assert result.code == 1
        assert result.mentions("app/composition/providers/process.py")

    def test_path_of_a_table_key_is_checked(self, repo: Repo) -> None:
        """В `per-file-ignores` путь стоит ключом, а не значением."""
        repo.pyproject(BASE + '\n[tool.example.per-file-ignores]\n"app/gone.py" = ["ANN"]\n')

        result = repo.run("check_config_paths")

        assert result.code == 1
        assert result.mentions("app/gone.py")

    def test_dotted_project_module_is_checked(self, repo: Repo) -> None:
        repo.pyproject(BASE + '\n[tool.example]\nlayer = "app.domain"\n')

        result = repo.run("check_config_paths")

        assert result.code == 1
        assert result.mentions("app/domain")

    def test_dotted_module_may_be_a_file(self, repo: Repo) -> None:
        """`app.interface.api.app` — это `app.py`, а не каталог."""
        repo.write("app/config.py")
        repo.pyproject(BASE + '\n[tool.example]\nmodule = "app.config"\n')

        result = repo.run("check_config_paths")

        assert result.code == 0

    def test_foreign_module_is_not_our_business(self, repo: Repo) -> None:
        repo.pyproject(BASE + '\n[tool.example]\nmodule = "taskiq.brokers.redis"\n')

        result = repo.run("check_config_paths")

        assert result.code == 0

    def test_path_relative_to_source_root_passes(self, repo: Repo) -> None:
        """Часть ключей отсчитывает путь от `app/`, а не от корня."""
        repo.mkdir("app/application/use_cases")
        repo.pyproject(BASE + '\n[tool.example]\nwhere = "application/use_cases"\n')

        result = repo.run("check_config_paths")

        assert result.code == 0

    def test_empty_pattern_only_warns(self, repo: Repo) -> None:
        repo.pyproject(BASE + '\n[tool.example]\nwhere = "app/**/*.sql"\n')

        result = repo.run("check_config_paths")

        assert result.code == 0
        assert result.mentions("Предупреждение")

    def test_pattern_without_its_root_only_warns(self, repo: Repo) -> None:
        repo.pyproject(BASE + '\n[tool.example]\nwhere = "nowhere/**"\n')

        result = repo.run("check_config_paths")

        assert result.code == 0
        assert result.mentions("nowhere")

    def test_exempt_key_is_not_checked(self, repo: Repo) -> None:
        repo.pyproject(
            """
            [tool.config_paths]
            files = ["pyproject.toml"]
            exempt = ["tool.example.where"]

            [tool.example]
            where = "app/gone.py"
            """
        )

        result = repo.run("check_config_paths")

        assert result.code == 0

    def test_missing_config_file_is_a_setup_error(self, repo: Repo) -> None:
        repo.pyproject('[tool.config_paths]\nfiles = ["pyproject.toml", "tach.toml"]\n')

        result = repo.run("check_config_paths")

        assert result.code == 2

    def test_bare_name_under_a_path_key_is_checked(self, repo: Repo) -> None:
        """`exclude_paths = ["migrations"]` — путь, хотя ни слэша, ни суффикса."""
        repo.pyproject(BASE + '\n[tool.example]\nexclude_paths = ["migrations"]\n')

        result = repo.run("check_config_paths")

        assert result.code == 1
        assert result.mentions("migrations")

    def test_bare_name_under_an_ordinary_key_is_a_word(self, repo: Repo) -> None:
        """`ref = "master"` — ветка, а не каталог: ключ не про пути."""
        repo.pyproject(BASE + '\n[tool.example]\nref = "master"\n')

        result = repo.run("check_config_paths")

        assert result.code == 0

    def test_key_name_itself_is_never_a_bare_path(self, repo: Repo) -> None:
        """`files = [...]` — имя ключа, а не каталог `files`."""
        repo.mkdir("app/domain")
        repo.pyproject(BASE + '\n[tool.example]\nsource_dirs = ["app/domain"]\n')

        result = repo.run("check_config_paths")

        assert result.code == 0
