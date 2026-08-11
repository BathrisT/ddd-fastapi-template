"""`.env.example` документирует конфиг целиком — и настроек, и compose.

Расхождение здесь не даёт ни красного теста, ни отказа линтера: оно ломает
старт у того, кто склонировал репозиторий, тогда как у автора правки значение
уже лежит в `.env`.
"""

from tests.unit.scripts.conftest import Repo

CONFIG = """
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class App(BaseModel):
    env: str = "development"
    secret: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")

    app: App
"""


class TestEnvExample:
    def test_every_field_is_described(self, repo: Repo) -> None:
        repo.write("app/config.py", CONFIG)
        repo.write(".env.example", "APP__ENV=development\nAPP__SECRET=\n")

        result = repo.run("check_env_example")

        assert result.code == 0
        assert result.mentions("OK")

    def test_missing_required_field_is_rejected(self, repo: Repo) -> None:
        repo.write("app/config.py", CONFIG)
        repo.write(".env.example", "APP__ENV=development\n")

        result = repo.run("check_env_example")

        assert result.code == 1
        assert result.mentions("APP__SECRET")
        assert result.mentions("обязательное")

    def test_optional_field_is_required_in_the_example_too(self, repo: Repo) -> None:
        """Полей без умолчания единицы; проверка, срабатывающая раз в год, не читается.

        Ручка, которой нет в примере, невидима всем, кто не открывал config.py.
        """
        repo.write("app/config.py", CONFIG)
        repo.write(".env.example", "APP__SECRET=\n")

        result = repo.run("check_env_example")

        assert result.code == 1
        assert result.mentions("APP__ENV")

    def test_compose_variable_must_be_documented(self, repo: Repo) -> None:
        """Настройки — не единственный читатель файла: compose берёт оттуда же."""
        repo.write("app/config.py", CONFIG)
        repo.write(".env.example", "APP__ENV=development\nAPP__SECRET=\n")
        repo.write(
            "docker-compose.yml",
            """
            services:
              api:
                ports:
                  - "${API__PORT:-8000}:8000"
            """,
        )

        result = repo.run("check_env_example")

        assert result.code == 1
        assert result.mentions("API__PORT")

    def test_escaped_dollar_is_not_a_variable(self, repo: Repo) -> None:
        """`$$` в compose — литерал доллара, а не подстановка."""
        repo.write("app/config.py", CONFIG)
        repo.write(".env.example", "APP__ENV=development\nAPP__SECRET=\n")
        repo.write(
            "docker-compose.yml",
            """
            services:
              redis:
                command: sh -c 'echo $$REDIS_PASSWORD'
            """,
        )

        result = repo.run("check_env_example")

        assert result.code == 0

    def test_compose_variable_present_in_the_example_passes(self, repo: Repo) -> None:
        repo.write("app/config.py", CONFIG)
        repo.write(".env.example", "APP__ENV=development\nAPP__SECRET=\nAPI__PORT=8000\n")
        repo.write(
            "docker-compose.yml",
            """
            services:
              api:
                ports:
                  - "${API__PORT:-8000}:8000"
            """,
        )

        result = repo.run("check_env_example")

        assert result.code == 0

    def test_stale_line_is_reported_but_does_not_block(self, repo: Repo) -> None:
        """Строку продолжают заполнять, а её не читает никто — сказать надо, ломать нет."""
        repo.write("app/config.py", CONFIG)
        repo.write(".env.example", "APP__ENV=development\nAPP__SECRET=\nOLD__THING=1\n")

        result = repo.run("check_env_example")

        assert result.code == 0
        assert result.mentions("не читает никто")

    def test_missing_example_file(self, repo: Repo) -> None:
        repo.write("app/config.py", CONFIG)

        result = repo.run("check_env_example")

        assert result.code == 2
        assert result.mentions("сверять не с чем")
