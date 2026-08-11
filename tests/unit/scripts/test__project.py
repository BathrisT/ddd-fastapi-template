"""Общая часть сторожей: корень исходников и опознание репозитория по имени.

Модуль общий на всех, поэтому и ошибка в нём общая на всех. Два его правила
стоят отдельного теста, потому что оба заведены после реального молчания:

* каталога из конфига нет → отказ, а не «OK» на пустом обходе;
* репозиторий опознают по имени типа сразу три сторожа, и разъехавшись, они
  считали бы репозиторием разное, не сообщая об этом ни один.
"""

import sys

from tests.unit.scripts.conftest import REAL_SCRIPTS, Repo

sys.path.insert(0, str(REAL_SCRIPTS))
from _project import is_repository_port, names_repository, plural


class TestSourceRoot:
    def test_missing_source_root_fails_loudly(self, repo: Repo) -> None:
        """Пустой скан — отказ, а не успех.

        Это худший из отказов сторожа: `rglob` по несуществующему каталогу
        возвращает пустоту, и проверка печатает «OK», не посмотрев ни на один
        файл. Проект с другой раскладкой получал бы зелёный `make precommit`
        при половине мёртвых проверок.
        """
        repo.pyproject('[tool.code_layout]\nsource_root = "src"\n')

        result = repo.run("check_file_length")

        assert result.code == 2
        assert result.mentions("ОШИБКА НАСТРОЙКИ")
        assert result.mentions("[tool.code_layout].source_root")

    def test_existing_source_root_is_scanned(self, repo: Repo) -> None:
        result = repo.run("check_file_length")

        assert result.code == 0


class TestNamesRepository:
    """Предикат общий у `check_composition`, `check_db_access` и соседей."""

    def test_plain_port_is_a_repository(self) -> None:
        assert names_repository("UserRepo")
        assert names_repository("UserRepository")

    def test_plural_is_a_repository(self) -> None:
        """`repos`/`repositories` есть в маркерах `check_n_plus_one`.

        Без них сторожа расходятся: для одного `UserRepos` — репозиторий, для
        другого нет, и порт во множественном числе можно внедрить прямо во вход.
        """
        assert names_repository("UserRepos")
        assert names_repository("UserRepositories")

    def test_repo_inside_a_word_is_not_a_repository(self) -> None:
        """`Repo` живёт внутри `Report` — сравнение по подстроке дало бы ложь."""
        assert not names_repository("ReportService")

    def test_producer_of_a_repository_is_still_named_one(self) -> None:
        """Отдаёт репозиторий — значит вход, попросивший его, взял репозиторий."""
        assert names_repository("SubscriptionRepoByPortal")
        assert names_repository("PortalAnchorTemplateRepoFactory")

    def test_only_the_port_itself_must_live_in_ports(self) -> None:
        """С фабрики репозитория расположение не спрашивают — она не порт."""
        assert is_repository_port("UserRepo")
        assert not is_repository_port("PortalAnchorTemplateRepoFactory")
        assert not is_repository_port("SubscriptionRepoByPortal")


class TestPlural:
    def test_agrees_the_numeral(self) -> None:
        assert plural(1, "голова", "головы", "голов") == "1 голова"
        assert plural(2, "голова", "головы", "голов") == "2 головы"
        assert plural(5, "голова", "головы", "голов") == "5 голов"
        assert plural(11, "голова", "головы", "голов") == "11 голов"
        assert plural(21, "голова", "головы", "голов") == "21 голова"
