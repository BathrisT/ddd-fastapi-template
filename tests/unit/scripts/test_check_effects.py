"""Порядок операций: публиковать после фиксации, работать с возвращённым объектом.

Единственный сторож про порядок, а не про форму, — и единственный класс ошибок,
который тесты приложения не ловят ПО КОНСТРУКЦИИ: в них предписан
`NoopEventPublisher`, который задач не создаёт вовсе, поэтому перепутанный
порядок строк оставляет тест зелёным.

Поэтому здесь важнее обычного проверить две формы, на которых сторож уже
ошибался: коммит в ветке с ранним `return` и коммит, живущий в замыкании.
"""

from tests.unit.scripts.conftest import Repo


class TestPublishAfterCommit:
    def test_publish_before_commit(self, repo: Repo) -> None:
        repo.write(
            "app/application/use_cases/register_user.py",
            """
            class RegisterUserUseCase:
                async def execute(self) -> None:
                    saved = await self._users_repo.save(user)
                    await self._events.publish(UserRegistered(saved.id))
                    await self._committer.commit()
            """,
        )

        result = repo.run("check_effects")

        assert result.code == 1
        assert result.mentions("не прикрыт коммитом")

    def test_commit_then_publish(self, repo: Repo) -> None:
        repo.write(
            "app/application/use_cases/register_user.py",
            """
            class RegisterUserUseCase:
                async def execute(self) -> None:
                    saved = await self._users_repo.save(user)
                    await self._committer.commit()
                    await self._events.publish(UserRegistered(saved.id))
            """,
        )

        result = repo.run("check_effects")

        assert result.code == 0

    def test_commit_in_a_branch_with_an_early_return(self, repo: Repo) -> None:
        """Коммит текстуально выше, а на пути к публикации его не было.

        Сравнение по номеру строки эту форму пропускало: коммит засчитывался
        «выше по тексту», хотя исполнение приходит к публикации мимо него.
        """
        repo.write(
            "app/application/use_cases/register_user.py",
            """
            class RegisterUserUseCase:
                async def execute(self, quick: bool) -> None:
                    if quick:
                        await self._committer.commit()
                        return
                    await self._events.publish(UserRegistered(1))
            """,
        )

        result = repo.run("check_effects")

        assert result.code == 1
        assert result.mentions("не прикрыт коммитом")

    def test_commit_in_every_branch_is_legal(self, repo: Repo) -> None:
        repo.write(
            "app/application/use_cases/register_user.py",
            """
            class RegisterUserUseCase:
                async def execute(self, quick: bool) -> None:
                    if quick:
                        await self._committer.commit()
                        await self._events.publish(UserRegistered(1))
                    else:
                        await self._committer.commit()
                        await self._events.publish(UserRegistered(2))
            """,
        )

        result = repo.run("check_effects")

        assert result.code == 0

    def test_commit_living_in_a_closure_does_not_cover(self, repo: Repo) -> None:
        """Строки замыкания выполнятся тогда, когда его позовут, а не там, где написаны.

        Считая их вместе с внешними, сторож засчитывал коммит из вложенной
        функции и маскировал настоящее нарушение порядка.
        """
        repo.write(
            "app/application/use_cases/register_user.py",
            """
            class RegisterUserUseCase:
                async def execute(self) -> None:
                    async def flush() -> None:
                        await self._committer.commit()

                    await self._events.publish(UserRegistered(1))
                    await self._committer.commit()
            """,
        )

        result = repo.run("check_effects")

        assert result.code == 1
        assert result.mentions("не прикрыт коммитом")

    def test_publish_without_any_commit_is_out_of_scope(self, repo: Repo) -> None:
        """Граница проведена намеренно: проверка внутрифункциональная.

        `commit()` в одном методе и `publish()` в вызывающем сторож не свяжет —
        это цена статики, зато нет ни одного ложного срабатывания.
        """
        repo.write(
            "app/interface/worker/handlers/users.py",
            """
            class Handler:
                async def handle(self) -> None:
                    await self._events.publish(UserRegistered(1))
            """,
        )

        result = repo.run("check_effects")

        assert result.code == 0


class TestStaleAfterSave:
    def test_id_read_from_the_passed_object(self, repo: Repo) -> None:
        repo.write(
            "app/application/use_cases/register_user.py",
            """
            class RegisterUserUseCase:
                async def execute(self) -> int:
                    user = User.register("a@b.c", "Аня")
                    await self._users_repo.save(user)
                    return user.id
            """,
        )

        result = repo.run("check_effects")

        assert result.code == 1
        assert result.mentions("остался нулевым")

    def test_result_of_save_is_used(self, repo: Repo) -> None:
        repo.write(
            "app/application/use_cases/register_user.py",
            """
            class RegisterUserUseCase:
                async def execute(self) -> int:
                    user = User.register("a@b.c", "Аня")
                    saved = await self._users_repo.save(user)
                    return saved.id
            """,
        )

        result = repo.run("check_effects")

        assert result.code == 0

    def test_loaded_entity_keeps_its_real_id(self, repo: Repo) -> None:
        """У загруженной сущности идентификатор настоящий — это не ошибка,
        а самая частая форма кода."""
        repo.write(
            "app/application/use_cases/rename_user.py",
            """
            class RenameUserUseCase:
                async def execute(self, user_id: int) -> int:
                    user = await self._users_repo.get_by_id(user_id)
                    await self._users_repo.save(user)
                    return user.id
            """,
        )

        result = repo.run("check_effects")

        assert result.code == 0


class TestEmptySource:
    def test_source_root_without_python_files(self, repo: Repo) -> None:
        """Ноль разобранных файлов — это отказ, а не «нарушений не найдено»."""
        (repo.root / "app" / "__init__.py").unlink()

        result = repo.run("check_effects")

        assert result.code == 2
        assert result.mentions("смотрит в пустоту")
