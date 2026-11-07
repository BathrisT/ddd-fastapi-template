"""UTF-8 и обратимые следы ошибочной перекодировки в текстовых файлах проекта."""

import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT
from precommit_cache import tree_paths

SUFFIXES = {
    ".py",
    ".md",
    ".rst",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".sh",
    ".ini",
    ".cfg",
    ".csv",
    ".sql",
}
NAMES = {"Makefile", "Dockerfile", ".gitignore", ".dockerignore", ".env.example"}
CYRILLIC_GARBAGE = re.compile(r"(?:[РС][^\s]){2,}")
LATIN_GARBAGE = re.compile(r"Ã.|Â.|â€|ðŸ|Ð.|Ñ.")


def mojibake(line: str) -> bool:
    for token in line.split():
        for encoding, pattern in (
            ("cp1251", CYRILLIC_GARBAGE),
            ("cp1252", LATIN_GARBAGE),
            ("latin1", LATIN_GARBAGE),
        ):
            if not pattern.search(token):
                continue
            try:
                restored = token.encode(encoding).decode("utf-8")
            except UnicodeError:
                # Кавычки или нормальный текст вокруг могут помешать восстановлению.
                for match in pattern.finditer(token):
                    try:
                        restored = match[0].encode(encoding).decode("utf-8")
                    except UnicodeError:
                        continue
                    if restored != match[0]:
                        return True
            else:
                if restored != token:
                    return True
    return False


def check_text_encoding() -> list[str]:
    errors: list[str] = []
    scanned = 0
    for relative in tree_paths(()):
        path = ROOT / relative
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in SUFFIXES and path.name not in NAMES:
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            lineno = path.read_bytes()[: exc.start].count(b"\n") + 1
            errors.append(f"{relative}:{lineno}: файл не в UTF-8")
            continue
        for lineno, line in enumerate(text.split("\n"), 1):
            invalid = any(
                char == "\ufffd"
                or (unicodedata.category(char) in {"Cc", "Cs"} and char not in "\t\r")
                for char in line
            )
            if invalid or mojibake(line):
                errors.append(
                    f"{relative}:{lineno}: повреждённый текст или следы ошибочной перекодировки"
                )
    if not scanned:
        errors.append("Нет текстовых файлов для проверки")
    return errors


def main() -> int:
    try:
        errors = check_text_encoding()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"ОШИБКА ЧТЕНИЯ: {exc}")
        return 2
    for error in errors:
        print(error)
    if not errors:
        print("Text encoding: UTF-8 и признаки повреждения текста проверены. OK.")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
