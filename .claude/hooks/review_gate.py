#!/usr/bin/env python3
"""PreToolUse gate for `git commit`: require a fresh semantic review of the diff.

Registered on the Bash tool in `.claude/settings.json`, so it fires on every
Bash call — it exits early (silently) for anything that isn't a `git commit`.
Only once a command is confirmed to be a commit does any of the below apply.

Deterministic checks (style/types/layers/tests/security) already run via
`make precommit` — this hook is not a replacement for that. For commits that
touch enough of `app/**/*.py` (see TRIVIAL_LINE_THRESHOLD below — smaller or
non-app diffs skip this entirely), it blocks `git commit` until the review
quorum is met: LENS_COUNT lenses × COPIES_PER_LENS independent passes each,
every pass leaving its own diff-hash-bound artifact
(`.claude/.review-lens-<lens>-<copy>.md`). Any working-tree edit changes the
hash and invalidates stale reviews.

Passes of one lens do NOT split the diff between them — every pass reviews all
of it, differing only in the order its pack presents the files. Splitting would
demand an answer to "who is accountable for this slice", and it would also make
the quorum below meaningless.

Quorum, not unanimity: a lens counts as clean when QUORUM of its
COPIES_PER_LENS passes say CLEAN. Passes of one lens share a mandate, so a
finding surfacing in exactly one of three is more likely noise than defect —
and with nine agents, demanding all-clean would produce MORE rounds, not fewer.
A lone finding isn't discarded: the lens files it in its journal
(`.review-lens-<lens>.notes.md`), and next round the other passes look there
first. Artifacts themselves must all be present, though — a missing one means
an agent never ran, and counting quorum over the survivors would accept a
partial review as a whole one.

There's deliberately no orchestrator-authored summary artifact (no
`.last-review.md`) — a single self-report is one file an agent could write
without ever spawning a subagent. Instead every pass writes its own artifact,
hash-stamped from the hash `_review.py` computes, with its own verdict.

Artifacts are written by `lens_verdict.py`, never with the Write tool. That
keeps one owner for the format and moves the "does this hash match the pack I
reviewed" check from the agent's eyes into code — but it also sidesteps a
practical failure: an agent writing the file a commit gate reads is, from
outside, indistinguishable from an agent manufacturing its own approval, and
the auto-mode classifier blocks it on that reading, non-deterministically. A
`permissions.allow` rule only softened that; `autoMode` classifier rules can't
be set from repo settings at all (only user/flag/managed sources are trusted,
precisely because a repo is controllable by whoever is being classified).

This is NOT cryptographic proof that subagents were actually spawned — a
static file has no way to prove who wrote it, and nothing here stops an agent
from writing all of them by hand. What it does guarantee is that every pass
(real or not) is staked to the exact same diff snapshot, closing the weaker but
real risk of reviews silently covering different tree states. If a finding is
real, either fix it (which changes the hash and forces a fresh round) or, if
the finding is judged wrong, bypass via the kill switch below — there is no
"dispute in the commit message" path baked into the gate itself.

See CLAUDE.md for the short version. Once a command is confirmed to be a
commit, an internal error in the gate logic below denies with diagnostics
rather than either silently allowing (that previously hid a real bug — see
git history) or permanently blocking (the kill switch still bypasses this
branch too).
"""

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review import COPIES_PER_LENS, LENS_COUNT, MAX_ROUNDS, QUORUM, Review  # noqa: E402

# Counts only added+deleted lines in app/**/*.py — that's the only code whose
# logic bugs this gate exists to catch. Docs, config, tests, tooling
# (.claude/**, scripts/**, Makefile, tests/**, *.md, ...) never count toward
# this threshold, however large the diff — a 500-line docs-only or
# review-gate-only change is still trivial by this gate's definition.
TRIVIAL_LINE_THRESHOLD = 20
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


def parse_artifact(text: str) -> dict[str, str]:
    record: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            record[key.strip()] = value.strip()
    return record


def lens_tally(claude_dir: Path, diff_hash: str, lens: int) -> tuple[int, int, list[str]]:
    """`(проходов на месте, из них CLEAN, чего не хватает)` для одной линзы."""
    present = 0
    clean = 0
    missing: list[str] = []
    for copy in range(1, COPIES_PER_LENS + 1):
        path = Review.artifact(claude_dir, lens, copy)
        if not path.exists():
            missing.append(f"{path.name} (нет файла)")
            continue
        record = parse_artifact(path.read_text(encoding="utf-8"))
        if record.get("lens") != str(lens) or record.get("copy") != str(copy):
            missing.append(f"{path.name} (не тот номер линзы или прохода внутри файла)")
            continue
        if record.get("reviewed_hash") != diff_hash:
            missing.append(f"{path.name} (хэш от другого снимка дерева)")
            continue
        present += 1
        if record.get("verdict") == "CLEAN":
            clean += 1
    return present, clean, missing


def quorum_reached(claude_dir: Path, diff_hash: str) -> tuple[bool, list[str]]:
    """Пропускать ли коммит, и если нет — чего не хватает.

    Проходы обязаны быть НА МЕСТЕ все: отсутствующий артефакт значит, что агент
    не отработал (умер, не запущен), и засчитывать кворум по двум уцелевшим —
    значит принимать неполное ревью за полное.

    А вот вердикт достаточно чистый у QUORUM из COPIES_PER_LENS. Проходы одной
    линзы одинаковы по мандату, поэтому находка, всплывшая ровно в одном из
    трёх, — скорее шум, чем дефект; требование единогласия при девяти агентах
    давало бы больше раундов, а не меньше. Одиночная находка при этом не
    исчезает: линза уносит её в свой журнал, и в следующем раунде остальные
    проходы посмотрят туда прицельно.
    """
    problems: list[str] = []
    for lens in range(1, LENS_COUNT + 1):
        present, clean, missing = lens_tally(claude_dir, diff_hash, lens)
        problems.extend(missing)
        if present == COPIES_PER_LENS and clean < QUORUM:
            problems.append(
                f"линза {lens}: CLEAN сказали {clean} прохода из {COPIES_PER_LENS}, "
                f"нужно {QUORUM}"
            )
    return not problems, problems


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

    base = Review.base(cwd)

    # Через общий модуль, а не своим разбором `--numstat`: тот шёл мимо
    # `core.quotepath=false`, и путь с не-ASCII приезжал экранированным
    # (`"app/\320\277….py"`). Проверка `startswith("app/")` на таком пути не
    # срабатывает — строки файла не попадали бы в счёт, порог не набирался, и
    # ГЕЙТ ПРОПУСКАЛ БЫ ПРАВКУ БЕЗ РЕВЬЮ. Молча: ни отказа, ни следа.
    changed = Review.changed_files(cwd, base)
    changed_files = [path for _, _, path in changed]
    if not changed_files:
        allow()  # nothing to diff — let git report "nothing to commit" itself

    total_app_lines = sum(
        added + deleted
        for added, deleted, path in changed
        if path.startswith("app/") and path.endswith(".py")
    )
    if total_app_lines < TRIVIAL_LINE_THRESHOLD:
        allow()

    diff_hash = Review.hash(cwd, base)

    passed, problems = quorum_reached(claude_dir, diff_hash)
    if passed:
        allow()

    agents = LENS_COUNT * COPIES_PER_LENS
    # Deny reason is deliberately pointer-only (hash/cwd/file list), never the
    # diff or prompt text itself — each reviewing subagent fetches its own
    # context (its pack, review_prompt.md, anything else it judges relevant)
    # via its own tool calls. That's cheap input for the subagent; embedding it
    # here would mean the orchestrating agent re-emitting large content as its
    # own (much pricier) output, once per subagent spawned.
    reason = (
        "Коммит заблокирован pre-commit review gate: кворум по этому diff'у не "
        "собран.\n\n"
        "Не хватает:\n  " + "\n  ".join(problems) + "\n\n"
        "Что сделать:\n"
        "1. Собери пакеты: `make review-pack`. Он кладёт в `.claude/` по файлу "
        f"на проход — весь дифф с окружением, разным порядком разделов, — и "
        "заводит журналы линз. Без него линзам нечего читать.\n"
        f"2. Запусти параллельно {agents} сабагентов (Agent tool с "
        'subagent_type: "review-lens" — не общий claude/general-purpose), '
        "несколько tool-use в ОДНОМ сообщении с run_in_background: false у "
        "каждого: в фоне ты получишь управление раньше, чем они закончат, и "
        "сможешь отредактировать дерево у них под руками. "
        f"Это {LENS_COUNT} линзы по {COPIES_PER_LENS} прохода: каждому дай "
        f"путь к репозиторию `{cwd}`, ссылку на .claude/hooks/review_prompt.md, "
        "номер линзы и номер прохода. Дифф, CLAUDE.md и прочее каждый читает "
        "сам — не вставляй их в промпт текстом. Отдельной строкой скажи, что "
        "записать вердикт через .claude/hooks/lens_verdict.py — часть "
        "поручения: у сабагента в транскрипте нет ни одного хода пользователя, "
        "и разрешение на действие он может взять только из промпта, которым "
        "его запустили.\n"
        f"3. Кворум: линза чистая, если CLEAN сказали {QUORUM} прохода из "
        f"{COPIES_PER_LENS}. Артефакты при этом обязаны быть все "
        f"{agents} — отсутствующий значит, что агент не отработал.\n"
        "4. Есть находки — чини (это изменит дифф и хэш, пакеты надо собрать "
        "заново). Находка ошибочна — объясни это пользователю и создай "
        ".claude/.review-gate-disabled, обязательно и явно сказав, что коммит "
        f"уходит без гейта и почему. Живёт {KILL_SWITCH_TTL_SECONDS // 60} "
        "минут от создания, дальше отвалится сам.\n"
        f"5. Раундов не больше {MAX_ROUNDS}. Раунд, где не пришло ни одной "
        "находки выше планки, — последний: дальше это уже не сходимость, а "
        "шум, и решать по остатку должен пользователь, а не цикл.\n\n"
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
            "2. Собери пакеты (`make review-pack`) и прогони ревью "
            "(.claude/hooks/review_prompt.md) на свой фикс "
            "— именно на правку самого гейта, даже если TRIVIAL_LINE_THRESHOLD его "
            "формально не потребует (правки этого файла — high-stakes).\n"
            "3. Повтори `git commit`.\n\n"
            "Если чинить сейчас некогда — создай .claude/.review-gate-disabled и "
            "обязательно скажи об этом пользователю, чтобы сломанный хук не "
            f"блокировал несвязанные коммиты (действует {KILL_SWITCH_TTL_SECONDS // 60} "
            "минут от создания, дальше отвалится само)."
        )
