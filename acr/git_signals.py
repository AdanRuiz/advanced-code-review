"""Git history signals (PB-7, PB-57; SPEC.md "Detección de plagio", Checklist 1.1 and 1.2).

Reads the history of the checked-out version in ``repos/<id>`` and writes
``reports/<id>/git-signals.json``: first and last commit, number of commits, whether there is a
single commit, the commits dated before ``evento.inicio`` or after ``evento.fin`` (plagiarism),
the lines changed by each commit and the share of the code added in the largest one (point
1.1), and the authors with their commits compared with ``integrantes`` (point 1.2).

Lines under dependency folders (``node_modules``, ``vendor``...) and in lock files do not count
as team code (SPEC.md "Plantillas, generadores y librerías"); they are reported apart. Merge
commits show no lines, as ``git log`` prints no diff for them. Author e-mails are only used to
match ``integrantes`` and are never written.

Whether the repository is a fork cannot be read from a clone: it needs the hosting service's
API, a network call SPEC.md does not allow yet, so ``fork`` stays ``null`` ("sin dato").

Only ``git log``/``git rev-parse`` are run, never the team's code. The ``.git`` folder of a
ZIP or local folder comes from the team, so git is pointed at it explicitly (it never walks up
to a parent repository) and settings that could run programs are switched off.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from acr.config import Event, Member, is_valid_team_id
from acr.scanoss import dependency_dir

DEFAULT_REPORTS_DIR = Path("reports")
SIGNALS_FILE = "git-signals.json"
GIT_TIMEOUT_SECONDS = 5 * 60
SCHEMA_VERSION = 2

Runner = Callable[..., subprocess.CompletedProcess]

# Settings that could make git start another program while reading the history.
_SAFE_CONFIG = [
    "-c", "core.fsmonitor=false",
    "-c", "log.showSignature=false",
    "-c", "core.pager=cat",
    "-c", "core.quotePath=true",
]

# One header line per commit, starting with a record separator; fields split by unit separators.
# Paths with control characters are quoted (core.quotePath), so no numstat line starts with it.
_RECORD, _FIELD = "\x1e", "\x1f"
_LOG_FORMAT = "--format=%x1e%H%x1f%aI%x1f%cI%x1f%an%x1f%ae"
_NUMSTAT = re.compile(r"(\d+|-)\t(\d+|-)\t(.+)")
_NOREPLY = re.compile(r"(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com", re.IGNORECASE)

# Lock files written by package managers: dependencies, not team code.
LOCK_FILES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "deno.lock",
        "composer.lock",
        "gemfile.lock",
        "podfile.lock",
        "pipfile.lock",
        "poetry.lock",
        "pdm.lock",
        "uv.lock",
        "cargo.lock",
        "go.sum",
        "mix.lock",
        "pubspec.lock",
        "packages.lock.json",
        "gradle.lockfile",
        "flake.lock",
    }
)


class GitSignalsError(Exception):
    """The git history could not be read; the message is shown to the reviewer."""


@dataclass(frozen=True)
class Commit:
    hash: str
    author_date: datetime
    committer_date: datetime
    author_name: str = ""
    author_email: str = ""
    files_changed: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    # Lines added or deleted under dependency folders or in lock files.
    excluded_lines: int = 0

    def to_json(self) -> dict[str, str]:
        return {
            "hash": self.hash,
            "author_date": self.author_date.isoformat(),
            "committer_date": self.committer_date.isoformat(),
        }


def _aware(value: datetime) -> datetime:
    """Event dates without a time zone are taken as the reviewer's local time."""
    return value if value.tzinfo is not None else value.astimezone()


def _git(runner: Runner | None, git_dir: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1"}
    try:
        return (runner or subprocess.run)(
            ["git", *_SAFE_CONFIG, f"--git-dir={git_dir}", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitSignalsError("git no está instalado o no está en el PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitSignalsError(f"git tardó más de {GIT_TIMEOUT_SECONDS // 60} minutos") from exc


def _new_path(path: str) -> str:
    """Destination of a ``--numstat`` path: ``a/{old => new}/f`` and ``old => new`` are renames."""
    path = path.strip('"')
    braces = re.fullmatch(r"(.*)\{(.*) => (.*)\}(.*)", path)
    if braces:
        return (braces.group(1) + braces.group(3) + braces.group(4)).replace("//", "/")
    return path.split(" => ", 1)[1] if " => " in path else path


def is_dependency_path(path: str) -> bool:
    """Files under a dependency folder and lock files do not count as team code."""
    return dependency_dir(path) is not None or PurePosixPath(path).name.lower() in LOCK_FILES


def _parse_header(line: str) -> dict[str, Any]:
    parts = line[len(_RECORD) :].split(_FIELD)
    if len(parts) != 5 or not parts[0]:
        raise GitSignalsError(f"salida inesperada de git log: {line!r}")
    try:
        author, committer = datetime.fromisoformat(parts[1]), datetime.fromisoformat(parts[2])
    except ValueError as exc:
        raise GitSignalsError(f"fecha inválida en git log: {line!r}") from exc
    return {
        "hash": parts[0],
        "author_date": author,
        "committer_date": committer,
        "author_name": parts[3].strip(),
        "author_email": parts[4].strip(),
        "files_changed": 0,
        "lines_added": 0,
        "lines_deleted": 0,
        "excluded_lines": 0,
    }


def _parse_log(output: str) -> list[Commit]:
    """Parse ``git log --numstat`` with ``_LOG_FORMAT`` headers; binary files count no lines."""
    drafts: list[dict[str, Any]] = []
    # Not splitlines(): it also breaks on the record and unit separators.
    for line in (raw.rstrip("\r") for raw in output.split("\n")):
        if line.startswith(_RECORD):
            drafts.append(_parse_header(line))
            continue
        if not line.strip():
            continue
        match = _NUMSTAT.fullmatch(line)
        if match is None or not drafts:
            raise GitSignalsError(f"salida inesperada de git log: {line!r}")
        draft = drafts[-1]
        added, deleted = (int(n) if n != "-" else 0 for n in match.group(1, 2))
        draft["files_changed"] += 1
        if is_dependency_path(_new_path(match.group(3))):
            draft["excluded_lines"] += added + deleted
        else:
            draft["lines_added"] += added
            draft["lines_deleted"] += deleted
    return [Commit(**draft) for draft in drafts]


def read_history(repo: Path, runner: Runner | None = None) -> list[Commit] | None:
    """Commits reachable from HEAD, newest first; ``None`` if ``repo`` has no git history.

    ``.git`` must be a real folder inside ``repo``: a ``.git`` file (``gitdir:`` link) could
    point anywhere on disk, so it is treated as no history.
    """
    git_dir = repo / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        return None
    head = _git(runner, git_dir, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"])
    if head.returncode != 0:
        return []
    log = ["log", "--topo-order", "--no-color", "--no-ext-diff", "--no-textconv", "--numstat", "-M"]
    result = _git(runner, git_dir, [*log, _LOG_FORMAT, "HEAD"])
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise GitSignalsError(f"git log falló: {detail[-1] if detail else f'código {result.returncode}'}")
    return _parse_log(result.stdout)


def _outside_reasons(commit: Commit, start: datetime, end: datetime) -> list[str]:
    reasons = []
    if commit.author_date < start or commit.committer_date < start:
        reasons.append("before_start")
    if commit.author_date > end or commit.committer_date > end:
        reasons.append("after_end")
    return reasons


def _normalize_name(value: str) -> str:
    """Case, accents and repeated spaces do not matter when comparing names."""
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.casefold().split())


def _noreply_login(email: str) -> str | None:
    """GitHub user of a ``[id+]user@users.noreply.github.com`` address."""
    match = _NOREPLY.fullmatch(email.strip())
    return match.group(1) if match else None


def match_member(name: str, email: str, members: Sequence[Member]) -> Member | None:
    """The member a commit author is, by GitHub user (noreply e-mail or name) or full name."""
    login = (_noreply_login(email) or "").casefold()
    plain_name = _normalize_name(name)
    for member in members:
        if member.github.casefold() in (login, name.strip().casefold()):
            return member
    for member in members:
        if plain_name and _normalize_name(member.nombre) == plain_name:
            return member
    return None


def _size(commits: list[Commit]) -> dict[str, Any]:
    """Point 1.1: lines changed by each commit and share of the added code in the largest one."""
    rows = [
        {
            "hash": commit.hash,
            "author_date": commit.author_date.isoformat(),
            "author": commit.author_name,
            "files_changed": commit.files_changed,
            "lines_added": commit.lines_added,
            "lines_deleted": commit.lines_deleted,
            "lines_changed": commit.lines_added + commit.lines_deleted,
            "excluded_lines": commit.excluded_lines,
        }
        for commit in commits
    ]
    total_added = sum(commit.lines_added for commit in commits)
    largest = None
    for row in rows:  # oldest first: on a tie the earliest commit is the largest
        if largest is None or row["lines_added"] > largest["lines_added"]:
            largest = row
    percent = round(100 * largest["lines_added"] / total_added, 1) if largest and total_added else None
    return {
        "lines_added": total_added,
        "lines_deleted": sum(commit.lines_deleted for commit in commits),
        "excluded_lines": sum(commit.excluded_lines for commit in commits),
        "largest_commit": (
            None
            if largest is None or percent is None
            else {
                "hash": largest["hash"],
                "author_date": largest["author_date"],
                "lines_added": largest["lines_added"],
                "percent_of_code": percent,
            }
        ),
        "largest_commit_percent": percent,
        "commits": rows,
    }


def _authors(commits: list[Commit], members: Sequence[Member] | None) -> dict[str, Any]:
    """Point 1.2: distinct authors and commits per author, compared with ``integrantes``."""
    authors: dict[tuple[str, str], dict[str, Any]] = {}
    for commit in commits:  # oldest first
        member = match_member(commit.author_name, commit.author_email, members or ())
        login = _noreply_login(commit.author_email)
        if member is not None:
            key = ("member", member.github.casefold())
        elif login:
            key = ("github", login.casefold())
        else:
            key = ("name", _normalize_name(commit.author_name) or commit.author_email.casefold())
        entry = authors.setdefault(
            key,
            {
                "name": commit.author_name,
                "github": member.github if member else login,
                "member": member is not None,
                "commits": 0,
                "lines_added": 0,
                "lines_deleted": 0,
            },
        )
        entry["commits"] += 1
        entry["lines_added"] += commit.lines_added
        entry["lines_deleted"] += commit.lines_deleted
    rows = sorted(authors.values(), key=lambda row: (-row["commits"], row["name"].casefold()))
    result: dict[str, Any] = {
        "author_count": len(rows),
        "single_author": len(rows) == 1,
        "authors": rows,
        "members": None,
        "members_without_commits": [],
        "authors_not_members": 0,
    }
    if members is not None:
        by_github = {row["github"].casefold(): row for row in rows if row["member"]}
        result["members"] = [
            {
                "name": member.nombre,
                "github": member.github,
                "commits": by_github.get(member.github.casefold(), {}).get("commits", 0),
            }
            for member in members
        ]
        result["members_without_commits"] = [m["github"] for m in result["members"] if not m["commits"]]
        result["authors_not_members"] = sum(1 for row in rows if not row["member"])
    return result


def build_signals(
    team_id: str,
    event: Event,
    commits: list[Commit] | None,
    members: Sequence[Member] | None = None,
) -> dict[str, Any]:
    """A commit is outside the event if its author or committer date is outside it.

    ``members`` are the team's ``integrantes``; ``None`` when ``equipos.yml`` does not list them.
    """
    start, end = _aware(event.inicio), _aware(event.fin)
    oldest_first = list(reversed(commits or []))
    signals: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "team_id": team_id,
        "event": {"start": start.isoformat(), "end": end.isoformat()},
        "has_git": commits is not None,
        "commit_count": len(oldest_first),
        "single_commit": len(oldest_first) == 1,
        "first_commit": None,
        "last_commit": None,
        "outside_event": {"before_start": 0, "after_end": 0, "commits": []},
        **_size(oldest_first),
        **_authors(oldest_first, members),
        # A clone does not say whether the repository is a fork; see the module docstring.
        "fork": None,
    }
    if not commits:
        return signals
    signals["first_commit"] = commits[-1].to_json()
    signals["last_commit"] = commits[0].to_json()
    outside = signals["outside_event"]
    for commit in oldest_first:
        reasons = _outside_reasons(commit, start, end)
        if reasons:
            for reason in reasons:
                outside[reason] += 1
            outside["commits"].append({**commit.to_json(), "reasons": reasons})
    return signals


def summarize(signals: dict[str, Any]) -> str:
    """One line for ``acr git-signals`` and ``acr analyze``."""
    if not signals["has_git"]:
        return "sin historial de git"
    outside = signals["outside_event"]
    text = (
        f"{signals['commit_count']} commit(s), {outside['before_start']} antes del inicio, "
        f"{outside['after_end']} después del cierre, {signals['author_count']} autor(es)"
    )
    if signals["largest_commit_percent"] is not None:
        text += f", {signals['largest_commit_percent']}% del código en el commit más grande"
    if signals["single_author"]:
        text += "; ALERTA: un solo autor"
    if signals["members_without_commits"]:
        text += f"; integrantes sin commits: {', '.join(signals['members_without_commits'])}"
    return text


def write_git_signals(
    team_id: str,
    event: Event,
    repos_dir: Path = Path("repos"),
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    runner: Runner | None = None,
    members: Sequence[Member] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Read ``repos_dir/<id>`` and write ``reports_dir/<id>/git-signals.json``."""
    if not is_valid_team_id(team_id):
        raise GitSignalsError(f"id inválido ({team_id!r})")
    repo = repos_dir / team_id
    if not repo.is_dir():
        raise GitSignalsError(f"no existe {repo.as_posix()}; corre 'acr fetch {team_id}'")
    signals = build_signals(team_id, event, read_history(repo, runner=runner), members)

    out_dir = reports_dir / team_id
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / SIGNALS_FILE
    staging = out_dir / f".{SIGNALS_FILE}.tmp"
    staging.write_text(json.dumps(signals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staging.replace(target)
    return target, signals
