"""Git history signals for plagiarism review (PB-7, SPEC.md "Detección de plagio").

Reads the history of the checked-out version in ``repos/<id>`` and writes
``reports/<id>/git-signals.json``: first and last commit, number of commits, whether there is a
single commit, and the commits dated before ``evento.inicio`` or after ``evento.fin``.

Only ``git log``/``git rev-parse`` are run, never the team's code. The ``.git`` folder of a
ZIP or local folder comes from the team, so git is pointed at it explicitly (it never walks up
to a parent repository) and settings that could run programs are switched off.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from acr.config import Event, is_valid_team_id

DEFAULT_REPORTS_DIR = Path("reports")
SIGNALS_FILE = "git-signals.json"
GIT_TIMEOUT_SECONDS = 5 * 60
SCHEMA_VERSION = 1

Runner = Callable[..., subprocess.CompletedProcess]

# Settings that could make git start another program while reading the history.
_SAFE_CONFIG = [
    "-c", "core.fsmonitor=false",
    "-c", "log.showSignature=false",
    "-c", "core.pager=cat",
]


class GitSignalsError(Exception):
    """The git history could not be read; the message is shown to the reviewer."""


@dataclass(frozen=True)
class Commit:
    hash: str
    author_date: datetime
    committer_date: datetime

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
            env=env,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitSignalsError("git no está instalado o no está en el PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitSignalsError(f"git tardó más de {GIT_TIMEOUT_SECONDS // 60} minutos") from exc


def _parse_log(output: str) -> list[Commit]:
    commits = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 3:
            raise GitSignalsError(f"salida inesperada de git log: {line!r}")
        try:
            author, committer = datetime.fromisoformat(parts[1]), datetime.fromisoformat(parts[2])
        except ValueError as exc:
            raise GitSignalsError(f"fecha inválida en git log: {line!r}") from exc
        commits.append(Commit(parts[0], author, committer))
    return commits


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
    result = _git(runner, git_dir, ["log", "--topo-order", "--no-color", "--format=%H %aI %cI", "HEAD"])
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


def build_signals(team_id: str, event: Event, commits: list[Commit] | None) -> dict[str, Any]:
    """A commit is outside the event if its author or committer date is outside it."""
    start, end = _aware(event.inicio), _aware(event.fin)
    signals: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "team_id": team_id,
        "event": {"start": start.isoformat(), "end": end.isoformat()},
        "has_git": commits is not None,
        "commit_count": len(commits or []),
        "single_commit": len(commits or []) == 1,
        "first_commit": None,
        "last_commit": None,
        "outside_event": {"before_start": 0, "after_end": 0, "commits": []},
    }
    if not commits:
        return signals
    signals["first_commit"] = commits[-1].to_json()
    signals["last_commit"] = commits[0].to_json()
    outside = signals["outside_event"]
    for commit in reversed(commits):
        reasons = _outside_reasons(commit, start, end)
        if reasons:
            for reason in reasons:
                outside[reason] += 1
            outside["commits"].append({**commit.to_json(), "reasons": reasons})
    return signals


def write_git_signals(
    team_id: str,
    event: Event,
    repos_dir: Path = Path("repos"),
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    runner: Runner | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Read ``repos_dir/<id>`` and write ``reports_dir/<id>/git-signals.json``."""
    if not is_valid_team_id(team_id):
        raise GitSignalsError(f"id inválido ({team_id!r})")
    repo = repos_dir / team_id
    if not repo.is_dir():
        raise GitSignalsError(f"no existe {repo.as_posix()}; corre 'acr fetch {team_id}'")
    signals = build_signals(team_id, event, read_history(repo, runner=runner))

    out_dir = reports_dir / team_id
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / SIGNALS_FILE
    staging = out_dir / f".{SIGNALS_FILE}.tmp"
    staging.write_text(json.dumps(signals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staging.replace(target)
    return target, signals
