"""У цепочки ревизий ровно одна голова.

Две головы ломают `alembic upgrade head`, но не дают ни красного теста, ни
отказа линтера: слияние, которое их породило, проходит чисто — миграции с
разных сторон лежат в разных файлах и не спорят. Падает это не там, где ошибку
внесли, а у следующего, кто поднимает окружение.
"""

from tests.unit.scripts.conftest import Repo

ALEMBIC_INI = """
[alembic]
script_location = migrations
"""


def revision(name: str, down: str | None) -> str:
    parent = f'"{down}"' if down else "None"
    return (
        f'"""{name}"""\n\n'
        f'revision = "{name}"\n'
        f"down_revision = {parent}\n"
        "branch_labels = None\n"
        "depends_on = None\n\n\n"
        "def upgrade() -> None: ...\n\n\n"
        "def downgrade() -> None: ...\n"
    )


def prepare(repo: Repo) -> None:
    repo.write("alembic.ini", ALEMBIC_INI)
    repo.mkdir("migrations/versions")


class TestMigrationHeads:
    def test_single_head_passes(self, repo: Repo) -> None:
        prepare(repo)
        repo.write("migrations/versions/0001_users.py", revision("0001", None))
        repo.write("migrations/versions/0002_welcome.py", revision("0002", "0001"))

        result = repo.run("check_migration_heads")

        assert result.code == 0

    def test_two_heads_are_rejected(self, repo: Repo) -> None:
        prepare(repo)
        repo.write("migrations/versions/0001_users.py", revision("0001", None))
        repo.write("migrations/versions/0002_ours.py", revision("0002", "0001"))
        repo.write("migrations/versions/0003_theirs.py", revision("0003", "0001"))

        result = repo.run("check_migration_heads")

        assert result.code == 1
        assert result.mentions("2 головы")
        assert result.mentions("alembic merge")

    def test_no_revisions_at_all_is_legal(self, repo: Repo) -> None:
        """Так выглядит репозиторий сразу после `alembic init`."""
        prepare(repo)

        result = repo.run("check_migration_heads")

        assert result.code == 0

    def test_broken_down_revision_is_reported(self, repo: Repo) -> None:
        """Опечатка в хэше рождает вторую цепочку — читать её нечем."""
        prepare(repo)
        repo.write("migrations/versions/0001_users.py", revision("0001", None))
        repo.write("migrations/versions/0002_welcome.py", revision("0002", "нет-такой"))

        result = repo.run("check_migration_heads")

        assert result.code == 1
        assert result.mentions("цепочку ревизий не удалось прочитать")

    def test_missing_alembic_ini_is_a_config_error(self, repo: Repo) -> None:
        """Тихо пропустить нельзя: непройденная проверка выглядит как пройденная."""
        result = repo.run("check_migration_heads")

        assert result.code == 2
        assert result.mentions("alembic.ini")
