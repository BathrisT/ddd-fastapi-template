"""Слой входа HTTP: в `routes/` живут маршруты и ничего кроме.

Правило белое (каждый файл ОБЯЗАН объявлять маршрут), а не чёрное, поэтому
проверяется и то, что схемы рядом со своим маршрутом чужаком не считаются, и
то, что агрегатор освобождён.
"""

from tests.unit.scripts.conftest import Repo

ROUTES = """
[tool.fastapi_routes]
route_dirs = ["app/interface/api/routes"]
http_methods = ["get", "post", "put", "patch", "delete"]
"""


class TestRoutesOnlyDeclareRoutes:
    def test_file_with_a_route_passes(self, repo: Repo) -> None:
        repo.pyproject(ROUTES)
        repo.write(
            "app/interface/api/routes/users.py",
            """
            from fastapi import APIRouter
            from pydantic import BaseModel

            router = APIRouter()


            class UserOut(BaseModel):
                id: int


            @router.get("/users")
            async def list_users() -> list[UserOut]:
                return []
            """,
        )

        result = repo.run("check_fastapi_routes")

        assert result.code == 0

    def test_file_without_a_route_is_a_stranger(self, repo: Repo) -> None:
        repo.pyproject(ROUTES)
        repo.write(
            "app/interface/api/routes/parsing.py",
            """
            class WebhookParser:
                def parse(self, payload: dict) -> dict:
                    return payload
            """,
        )

        result = repo.run("check_fastapi_routes")

        assert result.code == 1
        assert result.mentions("не объявляет ни одного маршрута")

    def test_aggregator_is_exempt(self, repo: Repo) -> None:
        repo.pyproject(ROUTES)
        repo.write(
            "app/interface/api/routes/__init__.py",
            """
            from fastapi import APIRouter

            from app.interface.api.routes.users import router as users_router

            router = APIRouter()
            router.include_router(users_router)
            """,
        )
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

        result = repo.run("check_fastapi_routes")

        assert result.code == 0

    def test_missing_route_dir_is_a_config_error(self, repo: Repo) -> None:
        """Опечатка в пути давала бы пустой обход и бодрое «OK»."""
        repo.pyproject(ROUTES)

        result = repo.run("check_fastapi_routes")

        assert result.code == 2
        assert result.mentions("[tool.fastapi_routes].route_dirs")

    def test_unconfigured_check_says_so(self, repo: Repo) -> None:
        """Молчание сторожа обязано быть слышным: «пропущена», а не «OK»."""
        result = repo.run("check_fastapi_routes")

        assert result.code == 0
        assert result.mentions("не настроен")


class TestDeferredAnnotations:
    def test_future_annotations_in_a_routes_module(self, repo: Repo) -> None:
        """Аннотация ответа станет строкой, и обёрнутый хендлер не даст её резолвить.

        Отказ максимально поздний: приложение стартует, схема рисуется, тесты
        зелёные — 500 приходит на сериализации ответа, когда сценарий уже
        отработал.
        """
        repo.pyproject(ROUTES)
        repo.write(
            "app/interface/api/routes/users.py",
            """
            from __future__ import annotations

            from fastapi import APIRouter

            router = APIRouter()


            @router.get("/users")
            async def list_users() -> list[str]:
                return []
            """,
        )

        result = repo.run("check_fastapi_routes")

        assert result.code == 1
        assert result.mentions("from __future__ import annotations")
