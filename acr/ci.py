"""``acr ci``: the GitHub Action mode (PB-62, PB-65, SPEC.md "GitHub Action para otros repositorios").

Validates **only** the repository of the workspace, the one of the pipeline's owner. It never
reads ``equipos.yml`` (if the workspace has one, it is ignored and the summary says so), never
clones or downloads other repositories and has no ``--config`` option: the event and the single
team are built in memory from the action's inputs, which arrive as ``ACR_*`` environment
variables.

Steps, reusing the local ones: copy of the workspace (read-only, as a local folder in
``equipos.yml``), git signals, scc, MegaLinter, SCANOSS (optional), AI review through OpenRouter
(off unless ``ACR_AI_REVIEW`` is ``true``) and the English report. Dolos is skipped: it compares
teams with each other. Everything is written under ``--out`` (default ``$RUNNER_TEMP/acr``),
never in the workspace, so a report can never end up in a commit. The team's code is never
executed.

Outputs: the checklist table in ``$GITHUB_STEP_SUMMARY``; ``report-path``, ``artifact-dir``,
``pass``, ``fail``, ``manual`` and ``errors`` (steps that failed) in ``$GITHUB_OUTPUT``; and an
artifact folder with the report, the JSON results and the SARIF files without code snippets.
``fail-on`` only looks at the deterministic marks (1.1, 1.2 and 4.3): an AI mark never fails the
pipeline, because the reviewed code could steer it.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acr import ai_review as ai_review_module
from acr.ai_review import AiReviewError, Settings, describe, write_ai_review
from acr.config import GITHUB_USER_RE, Event, Member, Team, is_valid_team_id
from acr.fetch import FetchError, fetch_team, remove_tree
from acr.git_signals import GitSignalsError, write_git_signals
from acr.megalinter import DEFAULT_IMAGE, DEFAULT_TIMEOUT_SECONDS, ENGINES, MegaLinterError, run_megalinter
from acr.report import (
    CHECKLIST_ITEMS,
    FAIL,
    MANUAL,
    PASS,
    REPORT_FILE,
    ReportError,
    checklist,
    load_sources,
    write_report,
)
from acr.review import REVIEW_FILE
from acr.scanoss import ScanossError, write_scanoss
from acr.scc import SccError, write_scc

FAIL_ON = ("none", "secrets", "fail")
# Marks computed by code from the tools, never by a model (SPEC.md: fail-on).
DETERMINISTIC_ITEMS = ("1.1", "1.2", "4.3")
TRUE_VALUES = frozenset({"true", "1", "yes"})
FALSE_VALUES = frozenset({"false", "0", "no", ""})
ARTIFACT_DIR = "artifact"
ARTIFACT_FILES = (
    REPORT_FILE,
    "scc.json",
    "git-signals.json",
    "scanoss.json",
    REVIEW_FILE,
    "megalinter.json",
    "megalinter.sarif",
    "trivy.sarif",
    "semgrep.sarif",
)
MAX_EVIDENCE_CHARS = 300
STEP_ERRORS = (GitSignalsError, SccError, MegaLinterError, ScanossError, AiReviewError, OSError, ValueError)
EXIT_OK = 0
EXIT_FAILED = 1  # fail-on triggered, or the repository could not be copied
EXIT_USAGE = 2  # invalid inputs


class CiError(Exception):
    """Invalid inputs of the action; shown in the log."""


@dataclass(frozen=True)
class CiOptions:
    workspace: Path
    out: Path
    repository: str
    event_name: str
    project: str
    objective: str
    start: datetime | None
    end: datetime | None
    members: tuple[Member, ...] | None
    scanoss: bool
    fail_on: str
    engine: str
    image: str
    timeout_seconds: int
    ai: Settings
    event_path: Path | None = None


@dataclass
class CiResult:
    exit_code: int
    team_id: str
    report_path: Path | None = None
    artifact_dir: Path | None = None
    counts: dict[str, int] = field(default_factory=dict)
    summary: str = ""
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- inputs


def team_id_for(repository: str) -> str:
    """``owner/My.Repo`` -> ``my-repo``: only lowercase letters, digits and hyphens."""
    name = repository.rsplit("/", 1)[-1].lower()
    team_id = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:64].strip("-")
    return team_id if is_valid_team_id(team_id) else "repo"


def parse_datetime(value: str, name: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CiError(
            f"{name}: fecha mal formada ({value!r}); usa ISO 8601, p. ej. 2026-11-01T09:00:00-06:00"
        ) from exc
    if parsed.tzinfo is None:
        raise CiError(f"{name}: falta la zona horaria ({value!r})")
    return parsed


def parse_members(text: str) -> tuple[Member, ...] | None:
    """One ``github-user: Name`` (or just ``github-user``) per line."""
    members = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        login, _, name = line.partition(":")
        login, name = login.strip().lstrip("@"), name.strip()
        if not GITHUB_USER_RE.fullmatch(login):
            raise CiError(f"members, línea {number}: usuario de GitHub inválido ({login!r})")
        members.append(Member(nombre=name or login, github=login))
    return tuple(members) or None


def parse_bool(value: str, name: str) -> bool:
    lowered = value.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise CiError(f"{name}: debe ser true o false ({value!r})")


def options_from_env(
    env: Mapping[str, str], workspace: str | None = None, out: str | None = None
) -> CiOptions:
    """The action's inputs (``ACR_*``) and GitHub's own variables (``GITHUB_*``, ``RUNNER_TEMP``)."""
    workspace = workspace or env.get("ACR_PATH") or env.get("GITHUB_WORKSPACE") or "."
    if not out:
        temp = env.get("RUNNER_TEMP")
        if not temp:
            raise CiError("falta --out (fuera de GitHub Actions no hay RUNNER_TEMP)")
        out = str(Path(temp) / "acr")
    repository = env.get("GITHUB_REPOSITORY") or Path(workspace).resolve().name
    repo_name = repository.rsplit("/", 1)[-1]

    start = parse_datetime(env.get("ACR_EVENT_START", ""), "event-start")
    end = parse_datetime(env.get("ACR_EVENT_END", ""), "event-end")
    if (start is None) != (end is None):
        raise CiError("event-start y event-end van juntos: da los dos o ninguno")
    if start and end and end < start:
        raise CiError("event-end es anterior a event-start")
    fail_on = env.get("ACR_FAIL_ON", "none").strip().lower() or "none"
    if fail_on not in FAIL_ON:
        raise CiError(f"fail-on debe ser {', '.join(FAIL_ON)} ({fail_on!r})")
    engine = env.get("ACR_ENGINE", "docker").strip() or "docker"
    if engine not in ENGINES:
        raise CiError(f"motor de contenedores no soportado ({engine!r})")
    try:
        ai = Settings.from_env(env)
    except AiReviewError as exc:
        raise CiError(str(exc)) from exc
    event_path = env.get("GITHUB_EVENT_PATH")
    return CiOptions(
        workspace=Path(workspace),
        out=Path(out),
        repository=repository,
        event_name=env.get("ACR_EVENT_NAME", "").strip() or repo_name,
        project=env.get("ACR_PROJECT", "").strip() or repo_name,
        objective=env.get("ACR_OBJECTIVE", "").strip() or "Not given",
        start=start,
        end=end,
        members=parse_members(env.get("ACR_MEMBERS", "")),
        scanoss=parse_bool(env.get("ACR_SCANOSS", "true"), "scanoss"),
        fail_on=fail_on,
        engine=engine,
        image=env.get("ACR_MEGALINTER_IMAGE", "").strip() or DEFAULT_IMAGE,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        ai=ai,
        event_path=Path(event_path) if event_path else None,
    )


# --------------------------------------------------------------------------- GitHub facts


def is_shallow(workspace: Path) -> bool:
    """``actions/checkout`` without ``fetch-depth: 0`` leaves ``.git/shallow``."""
    return (workspace / ".git" / "shallow").is_file()


def read_fork(event_path: Path | None) -> bool | None:
    """``repository.fork`` of the event that started the workflow, read locally (no API call)."""
    if event_path is None:
        return None
    try:
        event = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    repository = event.get("repository") if isinstance(event, dict) else None
    fork = repository.get("fork") if isinstance(repository, dict) else None
    return fork if isinstance(fork, bool) else None


def _is_inside(path: Path, parent: Path) -> bool:
    path, parent = path.resolve(), parent.resolve()
    return path == parent or parent in path.parents


# --------------------------------------------------------------------------- artifact


def _clean_sarif(value: Any) -> Any:
    """SARIF without code snippets, and anything that looks like a credential redacted."""
    if isinstance(value, dict):
        return {k: _clean_sarif(v) for k, v in value.items() if k not in ("snippet", "contextRegion")}
    if isinstance(value, list):
        return [_clean_sarif(item) for item in value]
    if isinstance(value, str):
        return ai_review_module.redact(value)
    return value


def build_artifact(team_dir: Path, target: Path) -> list[str]:
    """Copy the files of ``ARTIFACT_FILES`` that exist; the raw MegaLinter logs are not included."""
    remove_tree(target)
    target.mkdir(parents=True)
    copied = []
    for name in ARTIFACT_FILES:
        source = team_dir / name
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        if name.endswith(".sarif"):
            try:
                text = json.dumps(_clean_sarif(json.loads(text)), ensure_ascii=False, indent=1) + "\n"
            except json.JSONDecodeError:
                continue
        (target / name).write_text(text, encoding="utf-8")
        copied.append(name)
    return copied


# --------------------------------------------------------------------------- summary


def _short(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= MAX_EVIDENCE_CHARS:
        return text
    cut = text[:MAX_EVIDENCE_CHARS].rsplit(" ", 1)[0]
    return cut + " …"


def should_fail(marks: Mapping[str, Any], fail_on: str) -> list[str]:
    """Deterministic items that trigger ``fail-on``; empty if the pipeline passes."""
    if fail_on == "secrets":
        items = ("4.3",)
    elif fail_on == "fail":
        items = DETERMINISTIC_ITEMS
    else:
        return []
    return [item for item in items if item in marks and marks[item].result == FAIL]


def build_summary(
    opts: CiOptions, team_id: str, marks: Mapping[str, Any], notes: list[str], failing: list[str]
) -> str:
    counts = Counter(mark.result for mark in marks.values())
    lines = [
        f"## Advanced Code Review – {opts.repository}",
        "",
        f"**Checklist:** {counts[PASS]} pass · {counts[FAIL]} fail · {counts[MANUAL]} manual review",
        "",
        "> Draft from static analysis tools"
        + (" and an AI review" if any(n.startswith("AI review:") for n in notes) else "")
        + ". Every mark must be checked by a person; AI marks never fail the pipeline.",
        "",
        "| # | Item | Result | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for item, mark in marks.items():
        result = f"**{mark.result}**" if mark.result == FAIL else mark.result
        lines.append(f"| {item} | {CHECKLIST_ITEMS[item]} | {result} | {_short(mark.evidence)} |")
    if notes:
        lines += ["", "**Notes:**", "", *(f"- {note}" for note in notes)]
    if failing:
        lines += ["", f"**fail-on `{opts.fail_on}`:** item(s) {', '.join(failing)} failed."]
    lines += ["", "The full report (`reporte.md`) and the results are in the `acr-report` artifact."]
    return "\n".join(lines) + "\n"


def _append(path_value: str | None, text: str) -> None:
    if path_value:
        with open(path_value, "a", encoding="utf-8") as handle:
            handle.write(text)


def write_outputs(env: Mapping[str, str], result: CiResult) -> None:
    _append(env.get("GITHUB_STEP_SUMMARY"), result.summary)
    outputs = {
        "report-path": result.report_path.as_posix() if result.report_path else "",
        "artifact-dir": result.artifact_dir.as_posix() if result.artifact_dir else "",
        "pass": str(result.counts.get(PASS, 0)),
        "fail": str(result.counts.get(FAIL, 0)),
        "manual": str(result.counts.get(MANUAL, 0)),
        "errors": str(sum(": failed (" in note for note in result.notes)),
    }
    _append(env.get("GITHUB_OUTPUT"), "".join(f"{key}={value}\n" for key, value in outputs.items()))


# --------------------------------------------------------------------------- run


_WIDE_START = datetime(1970, 1, 1, tzinfo=UTC)
_WIDE_END = datetime(9999, 12, 31, tzinfo=UTC)


def _patch_signals(path: Path, *, fork: bool | None, shallow: bool, window: bool) -> None:
    signals = json.loads(path.read_text(encoding="utf-8"))
    signals["fork"] = fork
    if shallow:
        signals["shallow_clone"] = True
    if not window:
        signals["event"] = None
        signals["outside_event"] = None
    path.write_text(json.dumps(signals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_ci(opts: CiOptions, log=print) -> CiResult:
    """Run every step; a failing step is noted and the next one still runs."""
    team_id = team_id_for(opts.repository)
    result = CiResult(exit_code=EXIT_OK, team_id=team_id)
    notes = result.notes
    workspace = opts.workspace
    if not workspace.is_dir():
        raise CiError(f"no existe la carpeta a validar ({workspace.as_posix()})")
    if _is_inside(opts.out, workspace):
        raise CiError("--out no puede estar dentro del repositorio: los reportes nunca van al workspace")
    if (workspace / "equipos.yml").exists():
        notes.append("`equipos.yml` was ignored: the action only validates this repository.")
    shallow = is_shallow(workspace)
    if shallow:
        notes.append("Shallow clone: use `fetch-depth: 0` in `actions/checkout`; git signals have no data.")

    team = Team(
        id=team_id,
        nombre=opts.repository.rsplit("/", 1)[-1],
        proyecto=opts.project,
        objetivo=opts.objective,
        repo=str(workspace.resolve()),
        integrantes=opts.members,
    )
    window = opts.start is not None and opts.end is not None
    event = Event(nombre=opts.event_name, inicio=opts.start or _WIDE_START, fin=opts.end or _WIDE_END)
    repos_dir, reports_dir = opts.out / "repos", opts.out / "reports"
    temp_dir = opts.out / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        fetch_team(team, repos_dir)
    except (FetchError, OSError) as exc:
        log(f"error: no se pudo copiar el repositorio: {exc}")
        result.exit_code = EXIT_FAILED
        result.summary = f"## Advanced Code Review\n\nThe repository could not be copied: {exc}\n"
        return result
    log(f"{team_id}: copia en solo lectura -> {(repos_dir / team_id).as_posix()}")

    def step(name: str, action) -> None:
        try:
            message = action()
        except STEP_ERRORS as exc:
            notes.append(f"{name}: failed ({exc}).")
            log(f"{name}: error: {exc}")
        else:
            if message:
                log(f"{name}: {message}")

    def git_signals() -> str:
        path, _signals = write_git_signals(team_id, event, repos_dir, reports_dir, members=opts.members)
        _patch_signals(path, fork=read_fork(opts.event_path), shallow=shallow, window=window)
        return path.as_posix()

    def scc() -> str:
        return write_scc(team_id, repos_dir, reports_dir)[0].as_posix()

    def megalinter() -> str:
        run = run_megalinter(
            team_id, repos_dir, reports_dir, engine=opts.engine, image=opts.image,
            timeout=opts.timeout_seconds, temp_dir=temp_dir, open_permissions=True,
        )
        if run.leftover is not None:
            log(f"MegaLinter: aviso: no se pudo borrar la copia temporal {run.leftover}")
        return "con errores de linters" if run.returncode else "sin errores de linters"

    def scanoss() -> str:
        return write_scanoss(team_id, repos_dir, reports_dir)[0].as_posix()

    step("Git signals", git_signals)
    step("scc", scc)
    step("MegaLinter", megalinter)
    if opts.scanoss:
        step("SCANOSS", scanoss)
    else:
        notes.append("SCANOSS was turned off (`scanoss: false`).")

    reason = opts.ai.skip_reason()
    if reason and opts.ai.enabled:
        notes.append("AI review skipped: enabled but `openrouter-api-key` is empty (forks get no secrets).")
        log(f"AI review: {reason}")
    elif reason:
        log(f"AI review: {reason}")
    else:
        try:
            review = write_ai_review(team, opts.event_name, opts.ai, repos_dir, reports_dir)
        except AiReviewError as exc:
            notes.append(f"AI review: failed ({exc}); its items stay in manual review.")
            log(f"AI review: error: {exc}")
        else:
            cost = f", cost USD {review.cost:.4f}" if review.cost is not None else ""
            tokens = (
                f", {review.prompt_tokens or 0} input / {review.completion_tokens or 0} output tokens"
                if review.prompt_tokens is not None or review.completion_tokens is not None else ""
            )
            sent = f"{review.files_sent} file(s) sent"
            notes.append(f"AI review: draft by `{review.model}`, {sent}{tokens}{cost}.")
            log(f"AI review: {describe(review)}")

    notes.append("Dolos was skipped: it compares teams with each other.")
    try:
        result.report_path = write_report(team, event, reports_dir, dashboard_url="")
    except (ReportError, OSError) as exc:
        notes.append(f"Report: failed ({exc}).")
        log(f"Report: error: {exc}")

    marks = checklist(load_sources(team_id, reports_dir), team_id)
    result.counts = dict(Counter(mark.result for mark in marks.values()))
    try:
        result.artifact_dir = opts.out / ARTIFACT_DIR
        copied = build_artifact(reports_dir / team_id, result.artifact_dir)
        log(f"Artifact: {', '.join(copied) or 'vacío'}")
    except OSError as exc:
        notes.append(f"Artifact: failed ({exc}).")
        result.artifact_dir = None
    failing = should_fail(marks, opts.fail_on)
    if failing:
        result.exit_code = EXIT_FAILED
    result.summary = build_summary(opts, team_id, marks, notes, failing)
    return result


def main_ci(env: Mapping[str, str] | None = None, workspace: str | None = None, out: str | None = None,
            log=print) -> int:
    env = os.environ if env is None else env
    try:
        opts = options_from_env(env, workspace, out)
        result = run_ci(opts, log=log)
    except CiError as exc:
        log(f"acr ci: {exc}")
        _append(env.get("GITHUB_STEP_SUMMARY"), f"## Advanced Code Review\n\nInvalid input: {exc}\n")
        return EXIT_USAGE
    write_outputs(env, result)
    log(f"acr ci: {result.counts.get(PASS, 0)} pass · {result.counts.get(FAIL, 0)} fail · "
        f"{result.counts.get(MANUAL, 0)} manual review")
    return result.exit_code
