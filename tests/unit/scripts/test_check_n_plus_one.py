"""N+1: сторож сообщает КЛАСС РОСТА, а не важность.

Поэтому тестов про молчание здесь больше, чем про отказ: список, в котором
лежит заведомо безобидное, перестают читать целиком — и тогда пропадают и
настоящие находки.
"""

from tests.unit.scripts.conftest import Repo


class TestQuadraticGrowth:
    def test_read_in_a_loop_nested_in_an_unbounded_loop(self, repo: Repo) -> None:
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                def __init__(self, users_repo: UserRepo) -> None:
                    self._users = users_repo

                async def build(self) -> None:
                    for group in await self._users.list_groups():
                        for member in await self._users.list_members(group.id):
                            await self._users.get_by_id(member.id)
            """,
        )

        result = repo.run("check_n_plus_one")

        assert result.code == 1
        assert result.mentions("ОТКАЗ: квадратичный рост запросов")

    def test_type_from_the_constructor_is_the_main_signal(self, repo: Repo) -> None:
        """Поле зовут по домену (`self._users`), и роль хранилища в имени не видна.

        Сторож, смотревший только на имя, честно молчал на собственном шаблоне —
        и выглядело это как «N+1 не найдено».
        """
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                def __init__(self, *, users: UserRepo) -> None:
                    self._users = users

                async def build(self) -> None:
                    for group in await self._users.list_groups():
                        for member in await self._users.list_members(group.id):
                            await self._users.get_by_id(member.id)
            """,
        )

        result = repo.run("check_n_plus_one")

        assert result.code == 1


class TestLinearGrowth:
    def test_single_unbounded_loop_is_a_warning(self, repo: Repo) -> None:
        """Линейный рост бывает уместен — решает автор, но видеть обязан."""
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                def __init__(self, users_repo: UserRepo) -> None:
                    self._users = users_repo

                async def build(self) -> None:
                    for user in await self._users.list_recent(10):
                        await self._users.get_by_id(user.id)
            """,
        )

        result = repo.run("check_n_plus_one")

        assert result.code == 0
        assert result.mentions("ПРЕДУПРЕЖДЕНИЕ")

    def test_constant_outer_loop_keeps_growth_linear(self, repo: Repo) -> None:
        """`for attempt in range(3)` вложен, но произведение остаётся линейным."""
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                def __init__(self, users_repo: UserRepo) -> None:
                    self._users = users_repo

                async def build(self) -> None:
                    for attempt in range(3):
                        for user in await self._users.list_recent(10):
                            await self._users.get_by_id(user.id)
            """,
        )

        result = repo.run("check_n_plus_one")

        assert result.code == 0


class TestSilence:
    def test_loop_over_an_attribute_of_one_entity(self, repo: Repo) -> None:
        """`block.file_ids` ограничен по смыслу, даже если сам блок из репозитория."""
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                def __init__(self, blocks_repo: BlockRepo, files_repo: FileRepo) -> None:
                    self._blocks = blocks_repo
                    self._files = files_repo

                async def build(self) -> None:
                    block = await self._blocks.get_by_id(1)
                    for file_id in block.file_ids:
                        await self._files.get_by_id(file_id)
            """,
        )

        result = repo.run("check_n_plus_one")

        assert result.code == 0
        assert not result.mentions("ПРЕДУПРЕЖДЕНИЕ")

    def test_constant_arguments_inside_a_loop_are_not_n_plus_one(self, repo: Repo) -> None:
        """Одно чтение, случайно оказавшееся внутри цикла, — не N+1."""
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                def __init__(self, users_repo: UserRepo) -> None:
                    self._users = users_repo

                async def build(self) -> None:
                    for user in await self._users.list_recent(10):
                        settings = await self._users.get_by_id(1)
            """,
        )

        result = repo.run("check_n_plus_one")

        assert result.code == 0
        assert not result.mentions("ПРЕДУПРЕЖДЕНИЕ")

    def test_writing_in_a_loop_is_normal(self, repo: Repo) -> None:
        """Запись обновляет N сущностей — других вариантов нет."""
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                def __init__(self, users_repo: UserRepo) -> None:
                    self._users = users_repo

                async def build(self) -> None:
                    for user in await self._users.list_recent(10):
                        await self._users.save(user)
            """,
        )

        result = repo.run("check_n_plus_one")

        assert result.code == 0

    def test_marker_inside_another_word_does_not_count(self, repo: Repo) -> None:
        """`store` сидит внутри `restore`, `dal` — внутри `modal`.

        Сравнение по подстроке делало бы `self._restore_service.get_state(...)`
        ложным N+1.
        """
        repo.write(
            "app/application/services/report.py",
            """
            class Report:
                def __init__(self, restore_service: RestoreService) -> None:
                    self._restore_service = restore_service

                async def build(self, items) -> None:
                    for item in items:
                        await self._restore_service.get_state(item.id)
            """,
        )

        result = repo.run("check_n_plus_one")

        assert result.code == 0


class TestConfiguration:
    def test_exclude_silences_a_path(self, repo: Repo) -> None:
        """Осознанное исключение — путём в конфиге, а не пометкой по месту."""
        repo.pyproject(
            """
            [tool.query_loops]
            exclude = ["app/admin/*"]
            """
        )
        repo.write(
            "app/admin/report.py",
            """
            class Report:
                def __init__(self, users_repo: UserRepo) -> None:
                    self._users = users_repo

                async def build(self) -> None:
                    for group in await self._users.list_groups():
                        for member in await self._users.list_members(group.id):
                            await self._users.get_by_id(member.id)
            """,
        )

        result = repo.run("check_n_plus_one")

        assert result.code == 0

    def test_source_root_without_python_files(self, repo: Repo) -> None:
        (repo.root / "app" / "__init__.py").unlink()

        result = repo.run("check_n_plus_one")

        assert result.code == 2
        assert result.mentions("смотрит в пустоту")
