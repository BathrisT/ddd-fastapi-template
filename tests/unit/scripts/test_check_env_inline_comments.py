"""Инлайн-комментарии в env-файлах: где кончается значение, решает пробел."""

from tests.unit.scripts.conftest import Repo

PATTERNS = """
[tool.env_files]
patterns = [".env", ".env.*", "*.env"]
"""


class TestEnvInlineComments:
    def test_comment_after_value_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(PATTERNS)
        repo.write(".env.example", "APP__ENV=development   # development | production\n")

        result = repo.run("check_env_inline_comments")

        assert result.code == 1
        assert result.mentions(
            ".env.example:1: комментарий на одной строке со значением `APP__ENV`"
        )

    def test_own_line_comment_passes(self, repo: Repo) -> None:
        repo.pyproject(PATTERNS)
        repo.write(
            ".env.example",
            """
            # development | production
            APP__ENV=development
            """,
        )

        result = repo.run("check_env_inline_comments")

        assert result.code == 0

    def test_hash_glued_to_the_value_is_not_a_comment(self, repo: Repo) -> None:
        """`value#tail` комментарием не считает ни один читатель — значит и мы не считаем."""
        repo.pyproject(PATTERNS)
        repo.write(".env.example", "APP__TITLE=value#tail\n")

        result = repo.run("check_env_inline_comments")

        assert result.code == 0

    def test_hash_after_a_space_is_caught_even_in_a_password(self, repo: Repo) -> None:
        """Тот же пароль с пробелом теряет хвост молча — ради этого случая правило и есть."""
        repo.pyproject(PATTERNS)
        repo.write(".env.example", "DATABASE__PASSWORD=p@ss #1\n")

        result = repo.run("check_env_inline_comments")

        assert result.code == 1
        assert result.mentions("`DATABASE__PASSWORD`")

    def test_hash_inside_quotes_stays_a_value(self, repo: Repo) -> None:
        repo.pyproject(PATTERNS)
        repo.write(".env.example", 'APP__TITLE="номер # 1"\n')

        result = repo.run("check_env_inline_comments")

        assert result.code == 0

    def test_comment_after_a_closing_quote_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(PATTERNS)
        repo.write(".env.example", 'APP__TITLE="номер 1"   # заголовок\n')

        result = repo.run("check_env_inline_comments")

        assert result.code == 1
        assert result.mentions("`APP__TITLE`")

    def test_escaped_quote_does_not_open_a_value(self, repo: Repo) -> None:
        """`\\"` внутри кавычек не закрывает их: иначе `#` дальше сочли бы комментарием."""
        repo.pyproject(PATTERNS)
        repo.write(".env.example", 'APP__TITLE="цитата \\" и # решётка"\n')

        result = repo.run("check_env_inline_comments")

        assert result.code == 0

    def test_empty_value_with_a_comment_is_rejected(self, repo: Repo) -> None:
        repo.pyproject(PATTERNS)
        repo.write(".env.example", "APP__API_KEY= # пусто значит без проверки\n")

        result = repo.run("check_env_inline_comments")

        assert result.code == 1
        assert result.mentions("`APP__API_KEY`")

    def test_export_prefix_does_not_hide_the_name(self, repo: Repo) -> None:
        repo.pyproject(PATTERNS)
        repo.write(".env.example", "export APP__ENV=development # прод\n")

        result = repo.run("check_env_inline_comments")

        assert result.code == 1
        assert result.mentions("`APP__ENV`")

    def test_the_value_itself_is_never_printed(self, repo: Repo) -> None:
        """Отчёт читают в логах CI: секрету там делать нечего."""
        repo.pyproject(PATTERNS)
        repo.write(".env.example", "LLM__API_KEY=zzz-placeholder # ключ разработчика\n")

        result = repo.run("check_env_inline_comments")

        assert result.code == 1
        assert not result.mentions("zzz-placeholder")

    def test_every_matching_file_is_scanned(self, repo: Repo) -> None:
        """`*.env` и `.env.*` — оба шаблона, а не только тот, что в корне привычнее."""
        repo.pyproject(PATTERNS)
        repo.write(".env.example", "APP__ENV=development\n")
        repo.write("deploy/staging.env", "APP__ENV=production # временно\n")

        result = repo.run("check_env_inline_comments")

        assert result.code == 1
        assert result.mentions("deploy/staging.env:1")

    def test_skipped_dirs_are_not_our_business(self, repo: Repo) -> None:
        repo.pyproject(PATTERNS)
        repo.write(".env.example", "APP__ENV=development\n")
        repo.write(".venv/lib/package/.env", "FOREIGN=1 # чужое\n")

        result = repo.run("check_env_inline_comments")

        assert result.code == 0

    def test_envrc_is_a_shell_script_and_not_an_env_file(self, repo: Repo) -> None:
        repo.pyproject(PATTERNS)
        repo.write(".env.example", "APP__ENV=development\n")
        repo.write(".envrc", "export APP__ENV=development # direnv, это шелл\n")

        result = repo.run("check_env_inline_comments")

        assert result.code == 0

    def test_empty_scan_is_a_loud_failure(self, repo: Repo) -> None:
        """Ни одного файла — отказ настройки, а не бодрое «OK» по пустому скану."""
        repo.pyproject(PATTERNS)

        result = repo.run("check_env_inline_comments")

        assert result.code == 2
        assert result.mentions("ОШИБКА НАСТРОЙКИ")

    def test_patterns_come_from_the_config(self, repo: Repo) -> None:
        repo.pyproject('[tool.env_files]\npatterns = ["*.env"]\n')
        repo.write("deploy/staging.env", "APP__ENV=production\n")
        repo.write(".env.example", "APP__ENV=development # мимо шаблона\n")

        result = repo.run("check_env_inline_comments")

        assert result.code == 0
