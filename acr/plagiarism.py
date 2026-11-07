"""Plagiarism data of every team for the Plagio page (PB-22, SPEC.md "Dashboard local" > "5. Plagio").

Infinity reads one URL per query and cannot loop over the teams picked in the ``equipo``
variable, so this module gathers the three levels of SPEC.md "Detección de plagio" and the git
signals of every team into one file, ``reports/plagiarism.json``, and the panels filter its rows:

- ``teams``: per team, the totals of the cards and of the "Historial de git" table, plus which
  source files were available.
- ``similarity``: Dolos similarity between every two teams, in both directions (``team_a`` is
  the row of the matrix and ``team_b`` its column), from ``reports/dolos/pairs.csv``.
- ``team_pairs``: each pair of teams once (``team_a`` < ``team_b``) with ``similar`` = 1 if
  Dolos found similar files between them, for the "pares de equipos similares" card.
- ``external_code``: each SCANOSS match of ``reports/<id>/scanoss.json``: file, file or snippet
  match, %, project of origin, licenses and link.
- ``web_matches``: each project found on the web in ``reports/<id>/web-plagiarism.json``; the
  file is checked with the validator of PB-13 first and left out if it is not valid.

A match means similarity, not confirmed copying: the decision is the reviewer's. Nothing is
executed and nothing leaves the machine: it only reads files in ``reports/``.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acr.config import is_valid_team_id
from acr.review import WEB_PLAGIARISM_FILE, validate_web_plagiarism

DEFAULT_REPORTS_DIR = Path("reports")
PLAGIARISM_FILE = "plagiarism.json"
SCHEMA_VERSION = 1

TEAM_COLUMNS = (
    "team",
    "scanoss",
    "files_scanned",
    "files_matched",
    "matched_percent",
    "external_matches",
    "dolos",
    "similar_teams",
    "max_similarity",
    "web",
    "web_status",
    "web_matches",
    "web_before_event",
    "git_signals",
    "has_git",
    "commit_count",
    "single_commit",
    "first_commit",
    "before_start",
    "after_end",
    "fork",
)


class PlagiarismError(Exception):
    """The plagiarism summary could not be written; shown to the reviewer."""


def _read_json(path: Path) -> dict[str, Any] | None:
    """The JSON object at ``path``, or ``None`` if it is missing or unreadable."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_pairs(reports_dir: Path) -> list[dict[str, str]] | None:
    """Rows of ``reports/dolos/pairs.csv``, or ``None`` if it is missing or unreadable."""
    path = reports_dir / "dolos" / "pairs.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error):
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any) -> int:
    return int(_number(value))


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _empty_team(team_id: str) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(TEAM_COLUMNS, 0)
    row.update(
        team=team_id,
        scanoss=False,
        matched_percent=0.0,
        dolos=False,
        max_similarity=0.0,
        web=False,
        web_status="",
        git_signals=False,
        has_git=False,
        single_commit=False,
        first_commit="",
        fork=None,
    )
    return row


def _external_code(team_id: str, report: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, Any]]:
    totals = _dict(report.get("totals"))
    summary["scanoss"] = True
    summary["files_scanned"] = _int(totals.get("files_scanned"))
    summary["files_matched"] = _int(totals.get("files_matched"))
    summary["matched_percent"] = round(_number(totals.get("matched_percent")), 1)
    rows = []
    for match in (_dict(m) for m in _list(report.get("matches"))):
        project = " ".join(
            _text(match.get(key)) for key in ("vendor", "component", "version") if _text(match.get(key))
        )
        percent = match.get("matched_percent")
        rows.append(
            {
                "team": team_id,
                "file": _text(match.get("path")),
                "match": _text(match.get("match")),
                "matched_percent": None if percent is None else _number(percent),
                "project": project,
                "licenses": ", ".join(str(lic) for lic in _list(match.get("licenses"))),
                "url": _text(match.get("url")),
            }
        )
    summary["external_matches"] = len(rows)
    return rows


def _web_matches(team_id: str, data: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, Any]]:
    summary["web"] = True
    summary["web_status"] = _text(data.get("status"))
    rows = [
        {
            "team": team_id,
            "title": match["title"],
            "url": match["url"],
            "source": match["source"],
            "published": match["published"] or "",
            "before_event": match["before_event"],
            "similarity": match["similarity"],
            "status": summary["web_status"],
        }
        for match in data["matches"]
    ]
    summary["web_matches"] = len(rows)
    summary["web_before_event"] = sum(1 for row in rows if row["before_event"] is True)
    return rows


def _git(signals: dict[str, Any], summary: dict[str, Any]) -> None:
    outside = _dict(signals.get("outside_event"))
    summary["git_signals"] = True
    summary["has_git"] = signals.get("has_git") is True
    summary["commit_count"] = _int(signals.get("commit_count"))
    summary["single_commit"] = signals.get("single_commit") is True
    summary["first_commit"] = _text(_dict(signals.get("first_commit")).get("author_date"))
    summary["before_start"] = _int(outside.get("before_start"))
    summary["after_end"] = _int(outside.get("after_end"))
    # git-signals.json (PB-7) does not detect forks yet: shown as unknown, not as "no".
    fork = signals.get("fork")
    summary["fork"] = fork if isinstance(fork, bool) else None


def team_plagiarism(team_id: str, reports_dir: Path = DEFAULT_REPORTS_DIR) -> dict[str, list[dict[str, Any]]]:
    """Rows of the per-team sections of ``plagiarism.json`` (SCANOSS, web and git)."""
    if not is_valid_team_id(team_id):
        raise PlagiarismError(f"id inválido ({team_id!r})")
    team_dir = reports_dir / team_id
    summary = _empty_team(team_id)
    rows: dict[str, list[dict[str, Any]]] = {"teams": [summary], "external_code": [], "web_matches": []}

    scanoss = _read_json(team_dir / "scanoss.json")
    if scanoss is not None:
        rows["external_code"] = _external_code(team_id, scanoss, summary)

    web = _read_json(team_dir / WEB_PLAGIARISM_FILE)
    if web is not None and not validate_web_plagiarism(web, team_id):
        rows["web_matches"] = _web_matches(team_id, web, summary)

    signals = _read_json(team_dir / "git-signals.json")
    if signals is not None:
        _git(signals, summary)
    return rows


def _similarity(
    team_ids: list[str], pairs: list[dict[str, str]], teams: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Both directions of every Dolos pair between teams of ``team_ids``, and each pair once."""
    known = set(team_ids)
    matrix: list[dict[str, Any]] = []
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in pairs:
        a, b = _text(row.get("team_a")), _text(row.get("team_b"))
        if a == b or a not in known or b not in known:
            continue
        a, b = sorted((a, b))
        if (a, b) in unique:
            continue
        similarity = round(_number(row.get("similarity")), 4)
        file_pairs = _int(row.get("file_pairs"))
        unique[(a, b)] = {
            "team_a": a,
            "team_b": b,
            "similarity": similarity,
            "file_pairs": file_pairs,
            "similar": 1 if file_pairs > 0 else 0,
        }
        files = (_text(row.get("file_a")), _text(row.get("file_b")))
        if (a, b) != (_text(row.get("team_a")), _text(row.get("team_b"))):
            files = files[::-1]
        for mine, other, mine_file, other_file in ((a, b, *files), (b, a, *files[::-1])):
            matrix.append(
                {
                    "team_a": mine,
                    "team_b": other,
                    "similarity": similarity,
                    "file_pairs": file_pairs,
                    "longest_fragment": _int(row.get("longest_fragment")),
                    "file_a": mine_file,
                    "file_b": other_file,
                }
            )
            summary = teams[mine]
            summary["dolos"] = True
            summary["max_similarity"] = max(summary["max_similarity"], similarity)
            if file_pairs > 0:
                summary["similar_teams"] += 1
    matrix.sort(key=lambda r: (r["team_a"], r["team_b"]))
    pair_rows = sorted(unique.values(), key=lambda r: (-r["similarity"], r["team_a"], r["team_b"]))
    return matrix, pair_rows


def build_plagiarism(
    team_ids: Iterable[str],
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The content of ``reports/plagiarism.json`` for ``team_ids`` (in that order)."""
    team_ids = list(team_ids)
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (now or datetime.now(UTC).astimezone()).isoformat(timespec="seconds"),
        "teams": [],
        "similarity": [],
        "team_pairs": [],
        "external_code": [],
        "web_matches": [],
    }
    for team_id in team_ids:
        for section, rows in team_plagiarism(team_id, reports_dir).items():
            data[section].extend(rows)
    pairs = read_pairs(reports_dir)
    if pairs is not None:
        teams = {row["team"]: row for row in data["teams"]}
        data["similarity"], data["team_pairs"] = _similarity(team_ids, pairs, teams)
    return data


def write_plagiarism(
    team_ids: Iterable[str],
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    *,
    now: datetime | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Write ``reports/plagiarism.json`` atomically and return its path and content."""
    data = build_plagiarism(team_ids, reports_dir, now=now)
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / PLAGIARISM_FILE
    staging = reports_dir / f".{PLAGIARISM_FILE}.tmp"
    staging.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staging.replace(target)
    return target, data
