"""Раскладка по папкам: роли слоя, имена-помойки, размер каталога, место протоколов."""

from tests.unit.scripts.conftest import Repo

LAYERS = """
[tool.code_layout]
source_root = "app"
max_files_per_dir = 2
banned_folder_names = ["utils", "helpers"]

[tool.code_layout.layer_folders]
"app/domain" = ["models", "events"]
"app/application" = ["dto", "ports", "use_cases"]
"""


def prepare(repo: Repo) -> None:
    repo.pyproject(LAYERS)
    repo.write("app/domain/models/user.py", "class User: ...\n")
    repo.write("app/application/ports/user_repo.py", "class UserRepo: ...\n")


class TestLayerRoles:
    def test_subfolder_outside_the_role_list_is_rejected(self, repo: Repo) -> None:
        prepare(repo)
        repo.write("app/domain/messaging/channel.py", "class Channel: ...\n")

        result = repo.run("check_layer_folders")

        assert result.code == 1
        assert result.mentions("app/domain/messaging: не роль")

    def test_declared_roles_pass(self, repo: Repo) -> None:
        prepare(repo)

        result = repo.run("check_layer_folders")

        assert result.code == 0

    def test_missing_layer_is_reported_not_skipped(self, repo: Repo) -> None:
        """Слой назвали в конфиге — значит его наличие часть настройки.

        Тихий `continue` здесь означал бы правило, ссылающееся в пустоту, и
        зелёную галку на непроверенном слое.
        """
        repo.pyproject(LAYERS)
        repo.write("app/domain/models/user.py", "class User: ...\n")

        result = repo.run("check_layer_folders")

        assert result.code == 1
        assert result.mentions("app/application: слоя нет")


class TestBannedNames:
    def test_dump_folder_is_rejected_at_any_depth(self, repo: Repo) -> None:
        prepare(repo)
        repo.write("app/domain/models/utils/dates.py", "class Dates: ...\n")

        result = repo.run("check_layer_folders")

        assert result.code == 1
        assert result.mentions("app/domain/models/utils: имя папки ничего не называет")


class TestDirSize:
    def test_too_many_files_in_one_directory(self, repo: Repo) -> None:
        prepare(repo)
        for name in ("a", "b", "c"):
            repo.write(f"app/domain/events/{name}.py", "class Event: ...\n")

        result = repo.run("check_layer_folders")

        assert result.code == 1
        assert result.mentions("app/domain/events: 3 файлов при лимите 2")

    def test_init_does_not_count_toward_the_limit(self, repo: Repo) -> None:
        prepare(repo)
        repo.write("app/domain/events/__init__.py")
        for name in ("a", "b"):
            repo.write(f"app/domain/events/{name}.py", "class Event: ...\n")

        result = repo.run("check_layer_folders")

        assert result.code == 0


class TestProtocolsLiveInPorts:
    def test_protocol_outside_ports_is_rejected(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/use_cases/register.py",
            """
            from typing import Protocol


            class Notifier(Protocol):
                def notify(self) -> None: ...
            """,
        )

        result = repo.run("check_layer_folders")

        assert result.code == 1
        assert result.mentions("Protocol `Notifier` вне ports/")

    def test_generic_protocol_outside_ports_is_rejected(self, repo: Repo) -> None:
        """`class X(Protocol[T])` — база это Subscript.

        Прямой `getattr` по подписке даёт None, и дженерик-контракт вне
        `ports/` проходил молча.
        """
        prepare(repo)
        repo.write(
            "app/application/use_cases/register.py",
            """
            from typing import Protocol, TypeVar

            T = TypeVar("T")


            class Notifier(Protocol[T]):
                def notify(self, item: T) -> None: ...
            """,
        )

        result = repo.run("check_layer_folders")

        assert result.code == 1
        assert result.mentions("Protocol `Notifier` вне ports/")

    def test_protocol_inside_ports_is_fine(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/ports/notifier.py",
            """
            from typing import Protocol


            class Notifier(Protocol):
                def notify(self) -> None: ...
            """,
        )

        result = repo.run("check_layer_folders")

        assert result.code == 0

    def test_protocol_in_infrastructure_is_not_the_core_contract(self, repo: Repo) -> None:
        """Инфраструктура — адаптеры; шов между её классами это её дело."""
        prepare(repo)
        repo.write(
            "app/infrastructure/channels/backend.py",
            """
            from typing import Protocol


            class KnowledgeBackend(Protocol):
                def fetch(self) -> None: ...
            """,
        )

        result = repo.run("check_layer_folders")

        assert result.code == 0
