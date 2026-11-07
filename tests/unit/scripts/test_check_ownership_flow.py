"""Регрессии линз: поля объектов и пути управления сохраняют происхождение."""

from textwrap import indent

import pytest

from tests.unit.scripts.conftest import Repo, Run


def check(repo: Repo, source: str, call: str = "Service().change({})") -> Run:
    repo.write("app/application/services/flow.py", source)
    repo.write(
        "app/application/use_cases/run.py",
        "from app.application.services.flow import Service\n"
        "class RunUseCase:\n"
        "    def execute(self):\n"
        f"        {call}\n",
    )
    return repo.run("check_object_ownership")


@pytest.mark.parametrize("receiver", ["box", "self"])
def test_field_assignment_updates_alias(repo: Repo, receiver: str) -> None:
    setup = "box = Box()" if receiver == "box" else "self.obj = {}"
    result = check(
        repo,
        f"""
        class Box:
            pass
        class Service:
            def change(self, foreign):
                {setup}
                {receiver}.obj = foreign
                alias = {receiver}
                alias.obj['bad'] = 1
        """,
    )
    assert result.code == 1
    assert result.mentions("app/application/services/flow.py:")


def test_aliases_survive_branch_join(repo: Repo) -> None:
    result = check(
        repo,
        """
        class Box:
            pass
        class Service:
            def change(self, foreign, flag):
                box = Box()
                alias = box
                if flag:
                    marker = 1
                else:
                    marker = 2
                box.obj = foreign
                alias.obj['bad'] = marker
        """,
        "Service().change({}, True)",
    )
    assert result.code == 1


@pytest.mark.parametrize("container", [False, True])
def test_nested_alias_survives_branch_join(repo: Repo, container: bool) -> None:
    setup = "box = {'obj': Box()}" if container else "box = Box(); box.obj = Box()"
    entry = "box['obj']" if container else "box.obj"
    result = check(
        repo,
        f"""
        class Box:
            pass
        class Service:
            def change(self, foreign, flag):
                {setup}
                alias = {entry}
                if flag:
                    alias.marker = 1
                else:
                    alias.marker = 2
                alias.ref = foreign
                {entry}.ref['bad'] = 1
        """,
        "Service().change({}, True)",
    )
    assert result.code == 1


@pytest.mark.parametrize("loop", ["for _ in range(2):", "while remaining:"])
def test_loop_rechecks_origin_from_previous_iteration(repo: Repo, loop: str) -> None:
    result = check(
        repo,
        f"""
        class Service:
            def change(self, foreign):
                current = {{}}
                remaining = 2
                {loop}
                    current['bad'] = 1
                    current = foreign
                    remaining -= 1
        """,
    )
    assert result.code == 1


def test_loop_keeps_zero_iteration_path(repo: Repo) -> None:
    result = check(
        repo,
        """
        class Service:
            def change(self, foreign):
                current = foreign
                for _ in range(0):
                    current = {}
                current['bad'] = 1
        """,
    )
    assert result.code == 1


@pytest.mark.parametrize("guard", ["", " if flag"])
def test_match_keeps_unmatched_path(repo: Repo, guard: str) -> None:
    pattern = "'local'" if not guard else "_"
    result = check(
        repo,
        f"""
        class Service:
            def change(self, foreign, mode, flag):
                current = foreign
                match mode:
                    case {pattern}{guard}:
                        current = {{}}
                current['bad'] = 1
        """,
        "Service().change({}, 'other', False)",
    )
    assert result.code == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_constructor_merges_field_origins(repo: Repo, reverse: bool) -> None:
    first, second = ("{}", "foreign") if reverse else ("foreign", "{}")
    result = check(
        repo,
        f"""
        class Service:
            def __init__(self, foreign, flag):
                if flag:
                    self.obj = {first}
                else:
                    self.obj = {second}
            def change(self):
                self.obj['bad'] = 1
        """,
        f"Service({{}}, {not reverse}).change()",
    )
    assert result.code == 1


def test_constructor_index_keeps_both_possible_origins(repo: Repo) -> None:
    repo.write(
        "app/application/services/flow.py",
        """
        class Service:
            def __init__(self, foreign, flag):
                if flag:
                    self.obj = foreign
                else:
                    self.obj = {}
            def change(self):
                self.obj['bad'] = 1
        """,
    )
    repo.write(
        "app/application/use_cases/run.py",
        """
        from app.application.services.flow import Service
        class RunUseCase:
            def __init__(self, service: Service):
                self.service = service
            def execute(self):
                self.service.change()
        """,
    )
    assert repo.run("check_object_ownership").code == 1


def test_nested_write_preserves_outer_alias(repo: Repo) -> None:
    result = check(
        repo,
        """
        class Box:
            pass
        class Service:
            def change(self, foreign):
                box = Box()
                alias = box
                box.child = Box()
                box.child.marker = 1
                box.obj = foreign
                alias.obj['bad'] = 1
        """,
    )
    assert result.code == 1


def test_constructor_index_respects_unconditional_overwrite(repo: Repo) -> None:
    repo.write(
        "app/application/services/flow.py",
        """
        class Service:
            def __init__(self, foreign):
                self.obj = foreign
                self.obj = {}
            def change(self):
                self.obj['good'] = 1
        """,
    )
    repo.write(
        "app/application/use_cases/run.py",
        """
        from app.application.services.flow import Service
        class RunUseCase:
            def __init__(self, service: Service):
                self.service = service
            def execute(self):
                self.service.change()
        """,
    )
    result = repo.run("check_object_ownership")
    assert result.code == 0, result.output


@pytest.mark.parametrize("own", [False, True])
def test_nested_constructor_argument_keeps_actual_origin(repo: Repo, own: bool) -> None:
    argument = "{}" if own else "foreign"
    result = check(
        repo,
        f"""
        class Service:
            def __init__(self, foreign):
                self.obj = {{'child': foreign}}
            def change(self):
                self.obj['child']['value'] = 1
            def execute(self, foreign):
                Service({argument}).change()
        """,
        "Service({}).execute({})",
    )
    assert result.code == (0 if own else 1), result.output
    if not own:
        assert result.mentions("RunUseCase"), result.output


@pytest.mark.parametrize(
    "body",
    [
        "box = Box()\nbox.obj = {}\nalias = box\nalias.obj['good'] = 1",
        "current = {}\nfor _ in range(2):\n    current['good'] = 1\n    current = {}",
        "current = foreign\nmatch 'local':\n    case 'local':\n        current = {}\n    case _:\n        current = {}\ncurrent['good'] = 1",
    ],
)
def test_own_objects_remain_writable(repo: Repo, body: str) -> None:
    source = "class Box:\n    pass\nclass Service:\n    def change(self, foreign):\n"
    result = check(repo, source + indent(body, "        ") + "\n")
    assert result.code == 0, result.output
