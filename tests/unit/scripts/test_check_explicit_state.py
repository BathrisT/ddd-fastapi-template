"""Запрет скрытых механизмов изменения и ветвления по типу."""

import pytest

from tests.unit.scripts.conftest import Repo


@pytest.mark.parametrize("method", ["__get__", "__set__", "__getter__", "__setattr__", "__eq__"])
def test_dunder_definition_is_rejected(repo: Repo, method: str) -> None:
    repo.write("app/model.py", f"class Model:\n    def {method}(self): pass\n")
    result = repo.run("check_explicit_state")
    assert result.code == 1
    assert result.mentions("app/model.py:2:")
    assert result.mentions(method)


def test_init_and_dunder_attribute_reads_are_allowed(repo: Repo) -> None:
    repo.write(
        "app/model.py", "class Model:\n    def __init__(self): self.name = type(self).__name__\n"
    )
    assert repo.run("check_explicit_state").code == 0


@pytest.mark.parametrize("layer", ["application", "composition", "domain"])
@pytest.mark.parametrize(
    "expression", ["isinstance(x, int)", "builtins.isinstance(x, int)", "check(x, int)"]
)
def test_isinstance_in_restricted_layer(repo: Repo, layer: str, expression: str) -> None:
    repo.write(
        f"app/{layer}/probe.py",
        f"from builtins import isinstance as check\nimport builtins\n{expression}\n",
    )
    result = repo.run("check_explicit_state")
    assert result.code == 1
    assert result.mentions("isinstance")


def test_infrastructure_isinstance_is_allowed(repo: Repo) -> None:
    repo.write("app/infrastructure/probe.py", "isinstance(x, int)\n")
    assert repo.run("check_explicit_state").code == 0


def test_invalid_python_is_a_failure(repo: Repo) -> None:
    repo.write("app/broken.py", "def (:\n")
    assert repo.run("check_explicit_state").code != 0


def test_local_isinstance_alias_is_rejected(repo: Repo) -> None:
    repo.write("app/application/service.py", "check = isinstance\ncheck(value, int)\n")
    assert repo.run("check_explicit_state").code == 1


@pytest.mark.parametrize("layer", ["application", "composition", "domain"])
@pytest.mark.parametrize("module", ["builtins", "bi"])
@pytest.mark.parametrize("annotation", ["", ": object"])
def test_qualified_isinstance_alias_is_rejected(
    repo: Repo, layer: str, module: str, annotation: str
) -> None:
    imported = "import builtins" if module == "builtins" else "import builtins as bi"
    repo.write(
        f"app/{layer}/service.py",
        f"{imported}\nfirst{annotation} = {module}.isinstance\ncheck = first\ncheck(value, int)\n",
    )
    result = repo.run("check_explicit_state")
    assert result.code == 1
    assert result.mentions("isinstance")
