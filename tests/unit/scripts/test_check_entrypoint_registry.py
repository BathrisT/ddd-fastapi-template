"""Вход, не попавший в свой реестр, — это тихая потеря сообщения.

Отказа не будет: обработчик просто не существует для отправителя. Поэтому
проверяется и обратная сторона — что сторож не навязывает форму записи в
реестре, иначе его отключат при первом же рефакторинге самого реестра.
"""

from tests.unit.scripts.conftest import Repo

REGISTRY = """
[tool.entrypoint_registry]
pairs = [
    { entries = "app/interface/worker/handlers", registry = "app/composition/worker_tasks.py" },
]
"""


class TestEntrypointRegistry:
    def test_registered_handler_passes(self, repo: Repo) -> None:
        repo.pyproject(REGISTRY)
        repo.write(
            "app/interface/worker/handlers/users.py",
            """
            async def welcome_user(user_id: int) -> None: ...
            """,
        )
        repo.write(
            "app/composition/worker_tasks.py",
            """
            from app.interface.worker.handlers import users


            class WorkerTasks:
                TABLE = [users.welcome_user]
            """,
        )

        result = repo.run("check_entrypoint_registry")

        assert result.code == 0

    def test_forgotten_handler_is_reported(self, repo: Repo) -> None:
        repo.pyproject(REGISTRY)
        repo.write(
            "app/interface/worker/handlers/users.py",
            """
            async def welcome_user(user_id: int) -> None: ...


            async def purge_inactive_users() -> None: ...
            """,
        )
        repo.write(
            "app/composition/worker_tasks.py",
            """
            from app.interface.worker.handlers import users


            class WorkerTasks:
                TABLE = [users.welcome_user]
            """,
        )

        result = repo.run("check_entrypoint_registry")

        assert result.code == 1
        assert result.mentions("`purge_inactive_users` не назван реестром")

    def test_bare_name_in_the_registry_counts(self, repo: Repo) -> None:
        """Форму записи не навязываем — вопрос один: названо или забыто."""
        repo.pyproject(REGISTRY)
        repo.write(
            "app/interface/worker/handlers/users.py",
            """
            async def welcome_user(user_id: int) -> None: ...
            """,
        )
        repo.write(
            "app/composition/worker_tasks.py",
            """
            from app.interface.worker.handlers.users import welcome_user

            TABLE = [welcome_user]
            """,
        )

        result = repo.run("check_entrypoint_registry")

        assert result.code == 0

    def test_private_helper_is_not_an_entry(self, repo: Repo) -> None:
        repo.pyproject(REGISTRY)
        repo.write(
            "app/interface/worker/handlers/users.py",
            """
            async def welcome_user(user_id: int) -> None: ...


            def _build_text(name: str) -> str:
                return name
            """,
        )
        repo.write(
            "app/composition/worker_tasks.py",
            "TABLE = [welcome_user]\n",
        )

        result = repo.run("check_entrypoint_registry")

        assert result.code == 0

    def test_two_handlers_with_one_name_collide(self, repo: Repo) -> None:
        """Проводное имя задачи — имя функции: тёзка затирает чужую регистрацию."""
        repo.pyproject(REGISTRY)
        repo.write(
            "app/interface/worker/handlers/users.py",
            "async def cleanup() -> None: ...\n",
        )
        repo.write(
            "app/interface/worker/handlers/jobs.py",
            "async def cleanup() -> None: ...\n",
        )
        repo.write("app/composition/worker_tasks.py", "TABLE = [cleanup]\n")

        result = repo.run("check_entrypoint_registry")

        assert result.code == 1
        assert result.mentions("уже объявлен")

    def test_missing_registry_file_is_reported(self, repo: Repo) -> None:
        repo.pyproject(REGISTRY)
        repo.write("app/interface/worker/handlers/users.py", "async def welcome() -> None: ...\n")

        result = repo.run("check_entrypoint_registry")

        assert result.code == 1
        assert result.mentions("[tool.entrypoint_registry]")

    def test_unconfigured_check_says_so(self, repo: Repo) -> None:
        result = repo.run("check_entrypoint_registry")

        assert result.code == 0
        assert result.mentions("не настроен")
