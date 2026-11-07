"""Происхождение сохраняется через вызовы и вторые имена объектов."""

import pytest

from tests.unit.scripts.conftest import Repo


def service(repo: Repo, body: str) -> None:
    repo.write("app/application/services/service.py", body)


def scenario(repo: Repo, body: str) -> None:
    repo.write("app/application/use_cases/run.py", body)


@pytest.mark.parametrize(
    "write",
    [
        "obj.name = 'new'",
        "obj[1] = 2",
        "obj['key'] += 1",
        "replace(obj, name='new')",
        "dc.replace(obj, name='new')",
        "change(obj, name='new')",
    ],
)
def test_service_cannot_change_usecase_object(repo: Repo, write: str) -> None:
    service(
        repo,
        f"from dataclasses import replace as change, replace\nimport dataclasses as dc\nclass Service:\n    def change(self, obj):\n        alias = obj\n        {write.replace('obj', 'alias')}\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def execute(self):\n        obj = {}\n        Service().change(obj)\n",
    )
    result = repo.run("check_object_ownership")
    assert result.code == 1
    assert result.mentions("app/application/services/service.py:")


def test_creator_service_and_usecase_can_change(repo: Repo) -> None:
    service(
        repo,
        "class Service:\n    def make(self):\n        obj = {}\n        obj['one'] = 1\n        return obj\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def execute(self):\n        obj = Service().make()\n        obj['two'] = 2\n",
    )
    assert repo.run("check_object_ownership").code == 0


def test_another_service_cannot_change_service_object(repo: Repo) -> None:
    service(repo, "class Service:\n    def make(self): return {}\n")
    repo.write(
        "app/domain/services/other.py", "class Other:\n    def change(self, obj): obj['two'] = 2\n"
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nfrom app.domain.services.other import Other\nclass RunUseCase:\n    def execute(self): Other().change(Service().make())\n",
    )
    result = repo.run("check_object_ownership")
    assert result.code == 1
    assert result.mentions("app/domain/services/other.py:")


def test_port_result_stays_infrastructure_through_service(repo: Repo) -> None:
    repo.write(
        "app/application/ports/repo.py",
        "from typing import Protocol\nclass Repo(Protocol):\n    async def load(self) -> dict: ...\n",
    )
    service(
        repo,
        "from app.application.ports.repo import Repo\nclass Service:\n    def __init__(self, repo: Repo): self._repo = repo\n    async def load(self):\n        value = await self._repo.load()\n        value['bad'] = 1\n        return value\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def __init__(self, service: Service): self._service = service\n    async def execute(self):\n        value = await self._service.load()\n        value['good'] = 1\n",
    )
    result = repo.run("check_object_ownership")
    assert result.code == 1
    assert result.mentions("app/application/services/service.py:6:")
    assert not result.mentions("app/application/use_cases/run.py:6:")


def test_infrastructure_can_change_its_loaded_objects(repo: Repo) -> None:
    repo.write(
        "app/infrastructure/repo.py",
        "class Repo:\n    def __init__(self, session): self._session = session\n    async def load(self):\n        value = await self._session.get(Model, 1)\n        value.name = 'new'\n        return value\n",
    )
    assert repo.run("check_object_ownership").code == 0


def test_other_usecase_cannot_change_created_object(repo: Repo) -> None:
    scenario(
        repo,
        "class FirstUseCase:\n    def execute(self): return {}\nclass SecondUseCase:\n    def execute(self):\n        value = FirstUseCase().execute()\n        value['bad'] = 1\n",
    )
    assert repo.run("check_object_ownership").code == 1


def test_branches_do_not_hide_foreign_origin(repo: Repo) -> None:
    service(
        repo,
        "class Service:\n    def change(self, foreign, flag):\n        if flag:\n            value = {}\n        else:\n            value = foreign\n        value['bad'] = 1\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def execute(self): Service().change({}, True)\n",
    )
    assert repo.run("check_object_ownership").code == 1


def test_local_dict_and_string_replace_are_allowed(repo: Repo) -> None:
    service(
        repo,
        "class Service:\n    def make(self):\n        value = {}\n        value['one'] = 'old'.replace('old', 'new')\n        return value\n",
    )
    assert repo.run("check_object_ownership").code == 0


def test_configurable_source_root(repo: Repo) -> None:
    repo.pyproject('[tool.code_layout]\nsource_root = "src"\n')
    repo.write(
        "src/application/services/service.py",
        "class Service:\n    def make(self):\n        value = {}\n        value[1] = 2\n",
    )
    assert repo.run("check_object_ownership").code == 0


def test_unknown_source_is_not_silently_allowed(repo: Repo) -> None:
    service(repo, "class Service:\n    def change(self, obj): obj.name = 'new'\n")
    result = repo.run("check_object_ownership")
    assert result.code == 1
    assert result.mentions("происхождение")


def test_constructor_field_does_not_acquire_ownership(repo: Repo) -> None:
    service(
        repo,
        "class Service:\n    def __init__(self, obj): self.obj = obj\n    def change(self): self.obj.name = 'new'\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def execute(self): Service({}).change()\n",
    )
    assert repo.run("check_object_ownership").code == 1


def test_storing_foreign_value_in_local_container_keeps_origin(repo: Repo) -> None:
    service(
        repo,
        "class Service:\n    def change(self, foreign):\n        box = {'placeholder': {}}\n        alias = box\n        alias['obj'] = foreign\n        box['obj'].name = 'bad'\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def execute(self): Service().change({})\n",
    )
    assert repo.run("check_object_ownership").code == 1


def test_nested_function_cannot_hide_assignment(repo: Repo) -> None:
    service(
        repo,
        "class Service:\n    def change(self, obj):\n        def nested():\n            obj.name = 'bad'\n        nested()\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def execute(self): Service().change({})\n",
    )
    assert repo.run("check_object_ownership").code == 1


def test_port_container_elements_have_infrastructure_origin(repo: Repo) -> None:
    repo.write(
        "app/application/ports/repo.py",
        "from typing import Protocol\nclass Repo(Protocol):\n    async def load(self) -> list[dict]: ...\n",
    )
    scenario(
        repo,
        "from app.application.ports.repo import Repo\nclass RunUseCase:\n    def __init__(self, repo: Repo): self.repo = repo\n    async def execute(self):\n        values = await self.repo.load()\n        values[0]['good'] = 1\n",
    )
    assert repo.run("check_object_ownership").code == 0


def test_replace_reexport_keeps_identity(repo: Repo) -> None:
    repo.write("app/helpers.py", "from dataclasses import replace\n")
    service(
        repo,
        "from app.helpers import replace\nclass Service:\n    def change(self, obj): replace(obj, name='bad')\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def execute(self): Service().change({})\n",
    )
    assert repo.run("check_object_ownership").code == 1


def test_nested_function_alias_keeps_captured_object(repo: Repo) -> None:
    service(
        repo,
        "class Service:\n    def change(self, obj):\n        def nested(): obj.name = 'bad'\n        alias = nested\n        alias()\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def execute(self): Service().change({})\n",
    )
    assert repo.run("check_object_ownership").code == 1


def test_nested_container_alias_keeps_inserted_origin(repo: Repo) -> None:
    service(
        repo,
        "class Service:\n    def change(self, obj):\n        outer = {'inner': {'local': {}}}\n        inner = outer['inner']\n        inner['foreign'] = obj\n        outer['inner']['foreign'].name = 'bad'\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def execute(self): Service().change({})\n",
    )
    assert repo.run("check_object_ownership").code == 1


def test_known_container_index_does_not_merge_unrelated_objects(repo: Repo) -> None:
    service(
        repo,
        "class Service:\n    def change(self, obj):\n        box = [obj, {}]\n        box[1]['good'] = 1\n",
    )
    scenario(
        repo,
        "from app.application.services.service import Service\nclass RunUseCase:\n    def execute(self): Service().change({})\n",
    )
    assert repo.run("check_object_ownership").code == 0
