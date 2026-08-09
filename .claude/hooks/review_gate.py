#!/usr/bin/env python3
"""PreToolUse gate for `git commit`: require a fresh semantic review of the diff.

Registered on the Bash tool in `.claude/settings.json`, so it fires on every
Bash call — it exits early (silently) for anything that isn't a `git commit`.
Only once a command is confirmed to be a commit does any of the below apply.

Deterministic checks (style/types/layers/tests/security) already run via
`make precommit` — this hook is not a replacement for that. For commits that
touch enough of `app/**/*.py` (see TRIVIAL_LINE_THRESHOLD below — smaller or
non-app diffs skip this entirely), it blocks `git commit` until LENS_COUNT
diff-hash-bound artifacts (`.claude/.review-lens-<N>.md`) prove a
human-judgment review (logic, edge cases, concurrency, consistency) happened
on this *exact* diff. Any working-tree edit changes the hash and invalidates
stale reviews.

There's deliberately no orchestrator-authored summary artifact (no
`.last-review.md`) — a single self-report is one file an agent could write
without ever spawning a subagent. Instead each lens subagent writes its own
artifact, hash-stamped from its own `git diff HEAD`, with its own verdict.
The gate only allows the commit once all LENS_COUNT of them exist, match
the current diff hash, and say `verdict: CLEAN`.

This is NOT cryptographic proof that a subagent was actually spawned — a
static file has no way to prove who wrote it, and nothing here stops an
agent from writing all LENS_COUNT files by hand. What it does guarantee is
that all lenses (real or not) are staked to the exact same diff snapshot,
closing the weaker but real risk of lenses silently reviewing different tree
states — not a cryptographic proof of review, just protection against
forgetting to run one. If a lens finds something real, either fix it (which
changes the hash and forces a fresh round) or, if the finding is judged
wrong, bypass via the kill switch below — there is no "dispute in the
commit message" path baked into the gate itself.

See CLAUDE.md for the short version. Once a command is confirmed to be a
commit, an internal error in the gate logic below denies with diagnostics
rather than either silently allowing (that previously hid a real bug — see
git history) or permanently blocking (the kill switch still bypasses this
branch too).
"""

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# Counts only added+deleted lines in app/**/*.py — that's the only code whose
# logic bugs this gate exists to catch. Docs, config, tests, tooling
# (.claude/**, scripts/**, Makefile, tests/**, *.md, ...) never count toward
# this threshold, however large the diff — a 500-line docs-only or
# review-gate-only change is still trivial by this gate's definition.
TRIVIAL_LINE_THRESHOLD = 20
LENS_COUNT = 3
LENS_ARTIFACT_TEMPLATE = ".review-lens-{n}.md"
KILL_SWITCH_NAME = ".review-gate-disabled"
# A one-off bypass must not become a permanent, invisible one: the file is
# gitignored (`.claude/*`) and unbounded staleness had no reminder anywhere.
# 1h from mtime, not ctime — Windows' st_ctime is creation time, not "last
# touched", which would make an old-but-recently-rewritten switch read as
# fresh on Linux and stale on Windows. Deliberately not refreshed by use: a
# commit going through under an active switch does not extend the window —
# it's a bypass for the situation at hand, not a renewable "gate off" toggle.
KILL_SWITCH_TTL_SECONDS = 60 * 60

# Инструменты, через которые можно выполнить `git commit`. Список обязан
# совпадать с matcher'ом в .claude/settings.json — иначе гейт просто не
# позовут, и это не гипотетика: на Windows агент коммитил через PowerShell,
# пока хук висел только на Bash.
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})

# Matches a `git commit` invocation as its own command/segment (after start of
# string or a chain operator), tolerating a handful of leading global flags
# like `-C <path>`. Deliberately conservative — false negatives just mean the
# gate doesn't fire; false positives just mean an extra unnecessary review.
COMMIT_RE = re.compile(
    r"(?:^|[;&|]|\n)\s*(?:\S+=\S+\s+)*git\s+(?:-[A-Za-z-]+(?:[= ]\S+)?\s+)*commit\b"
)

# Staging chained into the same shell call as the commit (`git add -A && git
# commit -m ...`). This hook is PreToolUse: it runs BEFORE the command, so at
# check time those files are still untracked — and `git diff` never reports
# untracked files. A brand-new 300-line module would therefore count as zero
# changed lines, fall under TRIVIAL_LINE_THRESHOLD, and be committed without a
# single lens ever seeing it. Same class of hole as the PowerShell one in the
# note at the bottom of this file, just via the index instead of the shell.
#
# `git commit -a` is NOT this hole and deliberately isn't matched: it stages
# only already-tracked modifications, which `git diff HEAD` shows regardless of
# whether they're staged.
STAGING_RE = re.compile(r"(?:^|[;&|]|\n)\s*(?:\S+=\S+\s+)*git\s+(?:-[A-Za-z-]+(?:[= ]\S+)?\s+)*(?:add|stage)\b")

# This repo's convention: no AI co-authorship trailers in commit messages.
# Checked unconditionally — unlike the semantic review gate below, this is a
# plain deterministic rule with no false-positive risk, so it isn't subject
# to the kill switch or the trivial/doc-only bypasses.
COAUTHOR_RE = re.compile(r"co-authored-by", re.IGNORECASE)


def allow() -> None:
    sys.exit(0)


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def is_commit_command(command: str) -> bool:
    return bool(COMMIT_RE.search(command))


def run_git(cwd: str, *args: str) -> str:
    # Explicit encoding, not text=True's locale-based default: on a non-UTF-8
    # system locale (e.g. cp1251), decoding this repo's Cyrillic diff content
    # raises UnicodeDecodeError inside subprocess's reader thread, silently
    # turning result.stdout into None. check=True (not the original False):
    # a failed git invocation (missing binary, corrupt repo, ...) must raise,
    # not return empty output that reads identically to "no changes" and
    # silently allows the commit past every check below.
    result = subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def diff_base(cwd: str) -> str:
    """Tree-ish this gate diffs the working tree against — `HEAD`, or, in a
    repository without a single commit yet, the empty tree.

    `git diff HEAD` errors out with exit 128 while HEAD doesn't exist instead
    of reporting an everything-is-new diff, and with `check=True` below that
    took down the whole gate. Not a hypothetical corner: it's this template's
    own first commit, and every project generated from it hits the same state
    once — the one commit where a review matters most, since it carries the
    entire codebase.

    The empty tree's hash is asked of git rather than hardcoded: the familiar
    `4b825dc…` is the sha1 value, and a repository created with
    `--object-format=sha256` has a different one. `--stdin` with no stdin
    (DEVNULL, since this hook's own stdin already had the payload read out of
    it) hashes zero bytes, which is exactly an empty tree.
    """
    probe = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "-q", "--verify", "HEAD"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    if probe.returncode == 0:
        return "HEAD"
    empty_tree = subprocess.run(
        ["git", "-C", cwd, "hash-object", "-t", "tree", "--stdin"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        check=True,
    )
    return empty_tree.stdout.strip()


def git_diff_hash(cwd: str, base: str) -> str:
    # Hashes the raw bytes straight from git, not a decode-then-reencode
    # round trip: for a diff containing byte sequences that aren't valid
    # UTF-8, errors="replace" would collapse different invalid sequences
    # to the same U+FFFD stand-ins, letting two textually different diffs
    # hash identically — a stale lens artifact could then silently "cover"
    # unreviewed changes. Raw bytes have no such collision.
    #
    # CRLF normalized to LF before hashing: `git add` can rewrite a file's
    # line endings in the index per core.autocrlf/.gitattributes with zero
    # semantic change, which shifts `git diff HEAD`'s raw bytes and would
    # otherwise invalidate an already-CLEAN lens round for a no-op reason.
    result = subprocess.run(
        ["git", "-C", cwd, "diff", base],
        capture_output=True,
        check=True,
    )
    normalized = result.stdout.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()[:16]


def parse_artifact(text: str) -> dict[str, str]:
    record: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            record[key.strip()] = value.strip()
    return record


def all_lenses_clean(claude_dir: Path, diff_hash: str) -> bool:
    for n in range(1, LENS_COUNT + 1):
        lens_file = claude_dir / LENS_ARTIFACT_TEMPLATE.format(n=n)
        if not lens_file.exists():
            return False
        record = parse_artifact(lens_file.read_text(encoding="utf-8"))
        if (
            record.get("lens") != str(n)
            or record.get("reviewed_hash") != diff_hash
            or record.get("verdict") != "CLEAN"
        ):
            return False
    return True


def kill_switch_active(claude_dir: Path) -> bool:
    path = claude_dir / KILL_SWITCH_NAME
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return False
    # Fail-closed on a backward clock jump (NTP resync, VM snapshot restore):
    # a negative age must not read as "within TTL" and leave the switch
    # active indefinitely — that breaks the "expires in an hour" promise the
    # deny message makes.
    age = time.time() - mtime
    return 0 <= age <= KILL_SWITCH_TTL_SECONDS


def run_commit_gate(payload: dict, command: str) -> None:
    if COAUTHOR_RE.search(command):
        deny(
            "Коммит заблокирован: сообщение коммита содержит "
            "Co-Authored-By-трейлер.\n\n"
            "В этом репозитории коммиты не должны нести AI co-authorship "
            "атрибуцию — убери трейлер (и любую отсылающую к нему строку) "
            "из сообщения коммита и повтори `git commit` без него."
        )

    cwd = payload.get("cwd") or "."
    project_dir = Path(cwd)
    claude_dir = project_dir / ".claude"

    if kill_switch_active(claude_dir):
        allow()

    if STAGING_RE.search(command):
        deny(
            "Коммит заблокирован: индексация и коммит в одной команде.\n\n"
            "Гейт — PreToolUse, он смотрит дерево ДО выполнения твоей команды. "
            "Всё, что ты добавишь этим же вызовом, для него ещё untracked, а "
            "`git diff` untracked-файлы не показывает: новый модуль на 300 "
            "строк посчитается как ноль изменённых строк, пройдёт порог "
            "тривиальности и уедет в коммит, не попав ни в одну линзу — и в "
            "их дифф он тоже не попал бы.\n\n"
            "Что сделать: выполни `git add ...` ОТДЕЛЬНОЙ командой, а потом "
            "`git commit` — тогда гейт и линзы увидят ровно то, что "
            "коммитится."
        )

    base = diff_base(cwd)

    changed_files = [f for f in run_git(cwd, "diff", base, "--name-only").splitlines() if f]
    if not changed_files:
        allow()  # nothing to diff — let git report "nothing to commit" itself

    total_app_lines = 0
    for line in run_git(cwd, "diff", base, "--numstat").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted, path = parts[0], parts[1], parts[2]
        if not (path.startswith("app/") and path.endswith(".py")):
            continue
        total_app_lines += int(added) if added.isdigit() else 0
        total_app_lines += int(deleted) if deleted.isdigit() else 0
    if total_app_lines < TRIVIAL_LINE_THRESHOLD:
        allow()

    diff_hash = git_diff_hash(cwd, base)

    if all_lenses_clean(claude_dir, diff_hash):
        allow()

    # Deny reason is deliberately pointer-only (hash/cwd/file list), never the
    # diff or prompt text itself — each reviewing subagent fetches its own
    # context (git diff HEAD, review_prompt.md, anything else it judges
    # relevant) via its own tool calls. That's cheap input for the subagent;
    # embedding it here would mean the orchestrating agent re-emitting large
    # content as its own (much pricier) output, once per subagent spawned.
    reason = (
        "Коммит заблокирован pre-commit review gate: для этого diff'а нет "
        f"{LENS_COUNT} валидных lens-артефактов с verdict: CLEAN.\n\n"
        "Что сделать:\n"
        f"1. Запусти параллельно {LENS_COUNT} сабагента (Agent tool с "
        "subagent_type: \"review-lens\" — не общий claude/general-purpose, "
        "у review-lens во фронтматтере зафиксирован effort: medium "
        "независимо от эффорта текущей сессии; модель наследуется), "
        "несколько tool-use в ОДНОМ сообщении с run_in_background: false "
        "у каждого — не в фоне, иначе ты получишь управление обратно до "
        "того, как все линзы закончат, и сможешь случайно отредактировать "
        "дерево между их запуском и результатом). "
        f"Каждому дай: путь к репозиторию `{cwd}`, ссылку на "
        f".claude/hooks/review_prompt.md и номер его линзы (1..{LENS_COUNT} "
        "— определения линз внутри review_prompt.md). Diff, CLAUDE.md и любой "
        "другой контекст каждый читает сам — не вставляй их в промпт "
        "сабагента текстом. Каждая линза САМА считает свой reviewed_hash и "
        "пишет файл `.claude/.review-lens-<N>.md` с ЕЁ ЖЕ вердиктом (формат "
        "— в review_prompt.md) — гейт читает только эти файлы, никакого "
        "отдельного итогового артефакта от тебя не требуется и не "
        "принимается.\n"
        "2. Если хотя бы одна линза вернула находки — либо почини (это "
        "изменит diff, хэш и заставит перезапустить все линзы заново), либо, "
        "если находка ошибочна, объясни это пользователю и создай "
        ".claude/.review-gate-disabled — обязательно и явно сказав об этом "
        "пользователю, что за коммит уходит без прохождения гейта и почему. "
        f"Действует {KILL_SWITCH_TTL_SECONDS // 60} минут от времени "
        "создания файла (не продлевается повторным использованием), "
        "дальше гейт снова требует ревью — пересоздавать вручную не "
        "нужно, само отвалится.\n"
        "3. Если все линзы вернули CLEAN — просто повтори `git commit`.\n\n"
        f"Изменённые файлы: {', '.join(changed_files)}"
    )
    deny(reason)


if __name__ == "__main__":
    # This hook fires on every shell call, not just git commit — so failures
    # while we still don't know what command this is (bad JSON, unexpected
    # payload shape) must stay silent, not deny with a commit-flavored error
    # message about a `pytest`/`ls`/whatever call that was never a commit.
    #
    # ОБА шелла обязательны. Гейт долго висел только на Bash, а на Windows
    # агент коммитил через PowerShell — и целая фича уехала в master, ни разу
    # не пройдя ревью. Добавляете новый инструмент, умеющий выполнять команды, —
    # добавьте его и сюда, и в matcher в .claude/settings.json.
    try:
        _payload = json.load(sys.stdin)
        if _payload.get("tool_name") not in SHELL_TOOLS:
            sys.exit(0)
        _command = _payload.get("tool_input", {}).get("command", "")
        if not is_commit_command(_command):
            sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)

    # From here on the command is confirmed to be a git commit, so an
    # internal bug is worth the agent's attention (it means the gate ran
    # zero real checks) — surfaced as a deny with diagnostics rather than
    # swallowed. Still can't create a permanent deadlock: the kill switch
    # mentioned below bypasses this branch too.
    try:
        run_commit_gate(_payload, _command)
    except Exception as exc:
        deny(
            "Pre-commit review gate упал с внутренней ошибкой вместо того, чтобы "
            "решить allow/deny — это баг в самом хуке (.claude/hooks/review_gate.py), "
            "не в твоём diff'е:\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Что сделать:\n"
            "1. Разберись, в чём баг, и почини review_gate.py.\n"
            "2. Запусти 3-линзовое ревью (.claude/hooks/review_prompt.md) на свой фикс "
            "— именно на правку самого гейта, даже если TRIVIAL_LINE_THRESHOLD его "
            "формально не потребует (правки этого файла — high-stakes).\n"
            "3. Повтори `git commit`.\n\n"
            "Если чинить сейчас некогда — создай .claude/.review-gate-disabled и "
            "обязательно скажи об этом пользователю, чтобы сломанный хук не "
            f"блокировал несвязанные коммиты (действует {KILL_SWITCH_TTL_SECONDS // 60} "
            "минут от создания, дальше отвалится само)."
        )
