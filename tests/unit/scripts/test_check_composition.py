"""Граница внедрения: зависимости приходят из контейнера, а не из фреймворка.

Сторож заведён не против человека, а против ИДИОМЫ: все примеры FastAPI на
свете написаны через `Depends`, и агент пишет так же. Поэтому здесь же
проверяются обходные пути, которыми правило молча отключается, — псевдоним
импорта и точечная форма имени.
"""

from tests.unit.scripts.conftest import Repo

COMPOSITION = """
[tool.composition]
injection_markers = ["Depends", "Security"]
composition_roots = ["app/composition"]
verifiers = ["require_api_key"]
entry_roots = ["app/interface"]
container_types = ["AsyncContainer", "Container"]
container_attrs = ["dishka_container"]
route_class = "DishkaRoute"
injection_allowed = []
"""


def prepare(repo: Repo) -> None:
    repo.pyproject(COMPOSITION)
    repo.write("app/interface/__init__.py")


class TestFrameworkInjection:
    def test_depends_on_a_dependency_is_rejected(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/interface/api/routes/users.py",
            """
            from fastapi import APIRouter, Depends

            router = APIRouter(route_class=DishkaRoute)


            @router.get("/users")
            async def list_users(users_repo=Depends(get_user_repo)) -> list[str]:
                return []
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 1
        assert result.mentions("зависимость от фреймворка")

    def test_depends_on_a_verifier_is_the_entry_contract(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/interface/api/routes/users.py",
            """
            from typing import Annotated

            from fastapi import APIRouter, Depends

            router = APIRouter(route_class=DishkaRoute)

            CallerDep = Annotated[Caller, Depends(require_api_key)]


            @router.get("/users")
            async def list_users(caller: CallerDep) -> list[str]:
                return []
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 0

    def test_aliased_marker_is_the_same_marker(self, repo: Repo) -> None:
        """`from fastapi import Depends as Dep` обходил сверку по имени."""
        prepare(repo)
        repo.write(
            "app/interface/api/routes/users.py",
            """
            from fastapi import APIRouter
            from fastapi import Depends as Dep

            router = APIRouter(route_class=DishkaRoute)


            @router.get("/users")
            async def list_users(users_repo=Dep(get_user_repo)) -> list[str]:
                return []
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 1
        assert result.mentions("зависимость от фреймворка")


class TestRouterInjection:
    def test_leaf_router_without_route_class(self, repo: Repo) -> None:
        """Без класса маршрута `FromDishka` молча не сработает.

        Отказ наступит в проде и будет выглядеть как «параметр не пришёл».
        """
        prepare(repo)
        repo.write(
            "app/interface/api/routes/users.py",
            """
            from fastapi import APIRouter

            router = APIRouter()


            @router.get("/users")
            async def list_users() -> list[str]:
                return []
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 1
        assert result.mentions("route_class=DishkaRoute")

    def test_aggregator_without_routes_needs_no_route_class(self, repo: Repo) -> None:
        """`include_router` сохраняет класс ребёнка — агрегатору он бесполезен."""
        prepare(repo)
        repo.write(
            "app/interface/api/routes/__init__.py",
            """
            from fastapi import APIRouter

            router = APIRouter()
            router.include_router(users_router)
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 0

    def test_dotted_route_class_counts(self, repo: Repo) -> None:
        """`integrations.DishkaRoute` — тот же класс, просто через модуль."""
        prepare(repo)
        repo.write(
            "app/interface/api/routes/users.py",
            """
            from dishka.integrations import fastapi as integrations
            from fastapi import APIRouter

            router = APIRouter(route_class=integrations.DishkaRoute)


            @router.get("/users")
            async def list_users() -> list[str]:
                return []
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 0


class TestServiceLocator:
    def test_container_as_a_parameter(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/application/services/builder.py",
            """
            class Builder:
                def __init__(self, container: AsyncContainer) -> None:
                    self._container = container
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 1
        assert result.mentions("вне композиции")

    def test_container_from_request_state(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/interface/api/routes/users.py",
            """
            from fastapi import APIRouter

            router = APIRouter(route_class=DishkaRoute)


            @router.get("/users")
            async def list_users(request) -> list[str]:
                container = request.state.dishka_container
                return []
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 1
        assert result.mentions("контейнер добывается из запроса")

    def test_composition_may_hold_the_container(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/composition/container.py",
            """
            class AppContainer:
                @staticmethod
                def build() -> AsyncContainer: ...
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 0


class TestRepositoryAtEntry:
    def test_entry_asking_for_a_repository(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/interface/worker/handlers/users.py",
            """
            async def purge_users(users_repo: FromDishka[UserRepo]) -> None: ...
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 1
        assert result.mentions("вход берёт репозиторий вместо сценария")

    def test_entry_asking_for_a_scenario(self, repo: Repo) -> None:
        prepare(repo)
        repo.write(
            "app/interface/worker/handlers/users.py",
            """
            async def purge_users(purge: FromDishka[PurgeInactiveUsersUseCase]) -> None: ...
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 0

    def test_repository_producer_at_entry_counts_too(self, repo: Repo) -> None:
        """Отдаёт репозиторий — значит вход взял репозиторий."""
        prepare(repo)
        repo.write(
            "app/interface/worker/handlers/users.py",
            """
            async def purge_users(factory: FromDishka[UserRepoFactory]) -> None: ...
            """,
        )

        result = repo.run("check_composition")

        assert result.code == 1

    def test_missing_entry_root_is_a_config_error(self, repo: Repo) -> None:
        """Опечатка в пути давала пустой обход и зелёную галку."""
        repo.pyproject(COMPOSITION)

        result = repo.run("check_composition")

        assert result.code == 2
        assert result.mentions("[tool.composition].entry_roots")

    def test_unconfigured_check_says_so(self, repo: Repo) -> None:
        result = repo.run("check_composition")

        assert result.code == 0
        assert result.mentions("не настроен")
