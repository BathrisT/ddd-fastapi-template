"""Испорченный текст проверяется без запрета нормальной кириллицы."""

import subprocess

import pytest

from tests.unit.scripts.conftest import Repo


def git(repo: Repo, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo.root), *args], check=True, capture_output=True)


@pytest.fixture(autouse=True)
def initialize_git(repo: Repo) -> None:
    git(repo, "init", "--quiet")
    repo.write(".gitignore", ".scratchpad/\n.venv/\n")


@pytest.mark.parametrize(
    "path", ["README.md", "docs/guide.md", "app/model.py", "Makefile", ".env.example"]
)
def test_mojibake_is_rejected(repo: Repo, path: str) -> None:
    broken = "Привет мир".encode().decode("cp1251")
    repo.write(path, f"# {broken}\n")
    result = repo.run("check_text_encoding")
    assert result.code == 1
    assert result.mentions(f"{path}:1:")


def test_bad_utf8_is_rejected(repo: Repo) -> None:
    repo.write("docs/broken.md").write_bytes(b"\xff\xfe bad text")
    result = repo.run("check_text_encoding")
    assert result.code == 1
    assert result.mentions("UTF-8")


def test_replacement_character_is_rejected(repo: Repo) -> None:
    repo.write("README.md", "bad \ufffd text")
    assert repo.run("check_text_encoding").code == 1


def test_normal_multilingual_text_passes(repo: Repo) -> None:
    repo.write("README.md", "Русский текст: Россия, СРСР, РСФСР. Français, 日本語. © — →\n")
    assert repo.run("check_text_encoding").code == 0


def test_generated_files_and_binary_assets_are_skipped(repo: Repo) -> None:
    repo.write(".scratchpad/experiment/bad.md", "\ufffd")
    repo.write(".venv/lib/bad.py", "\ufffd")
    repo.write("logo.png").write_bytes(b"\xff\xfe")
    assert repo.run("check_text_encoding").code == 0


def test_custom_gitignore_rule_is_respected(repo: Repo) -> None:
    repo.write(".gitignore", "generated/\n")
    repo.write("generated/bad.md", "\ufffd")
    assert repo.run("check_text_encoding").code == 0


def test_nested_gitignore_negation_keeps_included_file(repo: Repo) -> None:
    repo.write("docs/.gitignore", "*.md\n!included.md\n")
    repo.write("docs/ignored.md", "\ufffd")
    repo.write("docs/included.md", "\ufffd")
    result = repo.run("check_text_encoding")
    assert result.code == 1
    assert result.mentions("docs/included.md:1:")
    assert not result.mentions("docs/ignored.md")


def test_tracked_file_is_scanned_even_when_gitignored(repo: Repo) -> None:
    repo.write(".gitignore", "tracked.md\n")
    repo.write("tracked.md", "\ufffd")
    git(repo, "add", "--force", "tracked.md")
    result = repo.run("check_text_encoding")
    assert result.code == 1
    assert result.mentions("tracked.md:1:")


def test_directory_name_without_gitignore_does_not_skip_files(repo: Repo) -> None:
    repo.write("build/bad.md", "\ufffd")
    result = repo.run("check_text_encoding")
    assert result.code == 1
    assert result.mentions("build/bad.md:1:")


def test_deleted_tracked_file_is_skipped(repo: Repo) -> None:
    path = repo.write("deleted.md", "\ufffd")
    git(repo, "add", "deleted.md")
    path.unlink()
    assert repo.run("check_text_encoding").code == 0
