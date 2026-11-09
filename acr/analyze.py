"""Run the whole static analysis for one or every team (PB-12, SPEC.md "Arquitectura y flujo").

Steps, in the order of the spec: fetch (``repos/<id>``), git signals, scc, MegaLinter and SCANOSS
per team, then Dolos once over every fetched team. The team code is never executed: each step
only hands the files to a static analysis tool.

Progress is written to ``reports/status.json`` (one entry per team: pendiente / obteniendo /
analizando / listo / error, last run times and a link to ``reports/<id>/analyze.log``). Only one
analysis per team runs at a time: each team is guarded by an OS file lock in ``reports/.locks/``
that is released automatically if the process dies, so a crashed run never blocks the next one.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from acr.config import Config, Team, is_valid_team_id
from acr.dolos import DEFAULT_IMAGE as DEFAULT_DOLOS_IMAGE
from acr.dolos import DolosError, write_dolos
from acr.fetch import DEFAULT_MAX_ZIP_BYTES, DEFAULT_REPOS_DIR, FetchError, fetch_team
from acr.git_signals import DEFAULT_REPORTS_DIR, GitSignalsError, write_git_signals
from acr.git_signals import summarize as summarize_git_signals
from acr.megalinter import DEFAULT_IMAGE, DEFAULT_TIMEOUT_SECONDS, MegaLinterError, run_megalinter
from acr.scanoss import ScanossError, write_scanoss
from acr.scc import SccError, write_scc

STATUS_FILE = "status.json"
LOCKS_DIR = ".locks"
LOG_FILE = "analyze.log"
SCHEMA_VERSION = 1
LOCK_POLL_SECONDS = 1.0

# Team states shown in the dashboard (SPEC.md "Página Configuración", "1. Resumen").
PENDING = "pendiente"
FETCHING = "obteniendo"
ANALYZING = "analizando"
DONE = "listo"
ERROR = "error"
RUNNING_STATES = (FETCHING, ANALYZING)

# Step results.
STEP_OK = "ok"
STEP_ERROR = "error"
STEP_SKIPPED = "omitido"

FETCH_STEP = "fetch"
DOLOS_STEP = "dolos"
STEP_ERRORS: tuple[type[Exception], ...] = (
    FetchError,
    GitSignalsError,
    SccError,
    MegaLinterError,
    ScanossError,
    DolosError,
    OSError,
)

Step = Callable[[Team], str]
DolosStep = Callable[[list[str]], str]
Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC).astimezone()


def _stamp(clock: Clock) -> str:
    return clock().isoformat(timespec="seconds")


# --- Locks -------------------------------------------------------------------------------------


def _try_lock(path: Path) -> IO[bytes] | None:
    """Take an exclusive, non-blocking OS lock on ``path``; ``None`` if someone else holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - the handle is the lock; closed by _unlock
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _unlock(handle: IO[bytes]) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()  # closing also releases a POSIX flock


@contextmanager
def _wait_lock(path: Path, poll: float = LOCK_POLL_SECONDS) -> Iterator[None]:
    """Hold the lock on ``path``, waiting for whoever has it."""
    while (handle := _try_lock(path)) is None:
        time.sleep(poll)
    try:
        yield
    finally:
        _unlock(handle)


def team_lock_path(reports_dir: Path, team_id: str) -> Path:
    if not is_valid_team_id(team_id):
        raise ValueError(f"id inválido ({team_id!r})")
    return reports_dir / LOCKS_DIR / f"{team_id}.lock"


def is_team_busy(reports_dir: Path, team_id: str) -> bool:
    """True if another analysis of ``team_id`` is running right now."""
    handle = _try_lock(team_lock_path(reports_dir, team_id))
    if handle is None:
        return True
    _unlock(handle)
    return False


# --- status.json -------------------------------------------------------------------------------


def _default_entry(team: Team) -> dict[str, Any]:
    return {
        "id": team.id,
        "nombre": team.nombre,
        "proyecto": team.proyecto,
        "state": PENDING,
        "step": None,
        "steps": {},
        "started_at": None,
        "finished_at": None,
        "error": None,
        "log": None,
    }


def read_status(reports_dir: Path = DEFAULT_REPORTS_DIR) -> dict[str, Any] | None:
    """Return ``reports/status.json`` or ``None`` if it is missing or unreadable."""
    try:
        data = json.loads((reports_dir / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def update_status(
    config: Config,
    reports_dir: Path,
    changes: dict[str, dict[str, Any]] | None = None,
    *,
    dolos: dict[str, Any] | None = None,
    held: frozenset[str] = frozenset(),
    clock: Clock = _now,
) -> dict[str, Any]:
    """Merge ``changes`` (per team id) into ``reports/status.json`` and write it atomically.

    Every team of ``config`` gets an entry (``pendiente`` if never analyzed); teams no longer in
    ``config`` are dropped. A team left in a running state whose lock is free (its process died)
    is marked as ``error``. ``held`` lists the teams locked by this process.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    with _wait_lock(reports_dir / LOCKS_DIR / "status.lock"):
        previous = read_status(reports_dir) or {}
        old_teams = {
            entry.get("id"): entry
            for entry in previous.get("teams", [])
            if isinstance(entry, dict)
        }
        teams = []
        for team in config.equipos:
            entry = _default_entry(team)
            old = old_teams.get(team.id)
            if isinstance(old, dict):
                entry.update({key: old[key] for key in entry if key in old})
            entry["nombre"], entry["proyecto"] = team.nombre, team.proyecto
            entry.update((changes or {}).get(team.id, {}))
            if (
                entry["state"] in RUNNING_STATES
                and team.id not in held
                and not is_team_busy(reports_dir, team.id)
            ):
                entry["state"] = ERROR
                entry["error"] = "análisis interrumpido"
            teams.append(entry)

        status = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": _stamp(clock),
            "teams": teams,
            "dolos": previous.get("dolos") if dolos is None else dolos,
        }
        target = reports_dir / STATUS_FILE
        staging = reports_dir / f".{STATUS_FILE}.tmp"
        staging.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staging.replace(target)
    return status


# --- Default steps -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    repos_dir: Path = DEFAULT_REPOS_DIR
    reports_dir: Path = DEFAULT_REPORTS_DIR
    max_zip_bytes: int = DEFAULT_MAX_ZIP_BYTES
    engine: str | None = None
    image: str = DEFAULT_IMAGE
    megalinter_timeout: int = DEFAULT_TIMEOUT_SECONDS
    dolos_image: str = DEFAULT_DOLOS_IMAGE


def default_steps(config: Config, options: Options) -> list[tuple[str, Step]]:
    """The per-team steps of SPEC.md "Arquitectura y flujo", in order."""
    repos, reports = options.repos_dir, options.reports_dir

    def fetch(team: Team) -> str:
        result = fetch_team(team, repos, max_zip_bytes=options.max_zip_bytes)
        return f"{result.kind} -> {result.path.as_posix()}"

    def git_signals(team: Team) -> str:
        path, signals = write_git_signals(team.id, config.evento, repos, reports, members=team.integrantes)
        return f"{summarize_git_signals(signals)} -> {path.as_posix()}"

    def scc(team: Team) -> str:
        path, report = write_scc(team.id, repos, reports)
        totals = report["totals"]
        return f"{totals['languages']} lenguaje(s), {totals['code']} línea(s) de código -> {path.as_posix()}"

    def megalinter(team: Team) -> str:
        result = run_megalinter(
            team.id,
            repos,
            reports,
            engine=options.engine,
            image=options.image,
            timeout=options.megalinter_timeout,
        )
        outcome = "con errores de linters" if result.returncode else "sin errores de linters"
        missing = f"; no se generó {', '.join(result.missing)}" if result.missing else ""
        return f"{outcome}, {len(result.files)} reporte(s){missing}"

    def scanoss(team: Team) -> str:
        path, report = write_scanoss(team.id, repos, reports)
        totals = report["totals"]
        return (
            f"{totals['files_matched']} de {totals['files_scanned']} archivo(s) con coincidencias "
            f"-> {path.as_posix()}"
        )

    return [
        (FETCH_STEP, fetch),
        ("git-signals", git_signals),
        ("scc", scc),
        ("megalinter", megalinter),
        ("scanoss", scanoss),
    ]


def default_dolos(options: Options) -> DolosStep:
    def dolos(team_ids: list[str]) -> str:
        result = write_dolos(
            team_ids,
            options.repos_dir,
            options.reports_dir,
            engine=options.engine,
            image=options.dolos_image,
        )
        totals = result.summary["totals"]
        return (
            f"{len(team_ids)} equipo(s), {totals['team_pairs_with_matches']} de {totals['team_pairs']} "
            f"par(es) con archivos similares -> {result.pairs_path.as_posix()}"
        )

    return dolos


# --- Pipeline ----------------------------------------------------------------------------------


@dataclass
class AnalysisResult:
    analyzed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    busy: list[str] = field(default_factory=list)
    dolos: str | None = None

    @property
    def ok(self) -> bool:
        return not self.failed and not self.busy and self.dolos != STEP_ERROR


class _TeamLog:
    """Per-run log in ``reports/<id>/analyze.log``; every line is also echoed."""

    def __init__(self, path: Path, team_id: str, clock: Clock, echo: Callable[[str], None]):
        self.path, self.team_id, self.clock, self.echo = path, team_id, clock, echo
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def write(self, message: str) -> None:
        with self.path.open("a", encoding="utf-8") as log:
            log.write(f"{_stamp(self.clock)} {message}\n")
        self.echo(f"{self.team_id}: {message}")


def _fetched(config: Config, repos_dir: Path) -> list[str]:
    return [team.id for team in config.equipos if (repos_dir / team.id).is_dir()]


def run_analysis(
    config: Config,
    teams: Sequence[Team],
    *,
    steps: Sequence[tuple[str, Step]],
    dolos: DolosStep | None,
    repos_dir: Path = DEFAULT_REPOS_DIR,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    clock: Clock = _now,
    echo: Callable[[str], None] = print,
    warn: Callable[[str], None] = print,
) -> AnalysisResult:
    """Run ``steps`` in order for each team in ``teams``, then ``dolos`` over every fetched team.

    A team already being analyzed by another process is skipped (``busy``). If the first step
    (fetch) fails the team stops there; a later step failing does not stop the other steps.
    """
    result = AnalysisResult()
    locks: dict[str, IO[bytes]] = {}
    for team in teams:
        handle = _try_lock(team_lock_path(reports_dir, team.id))
        if handle is None:
            result.busy.append(team.id)
            warn(f"{team.id}: ya hay un análisis en curso para este equipo; no se lanza otro")
        else:
            locks[team.id] = handle
    held = frozenset(locks)

    def status(changes: dict[str, dict[str, Any]], **kwargs: Any) -> None:
        update_status(config, reports_dir, changes, held=held, clock=clock, **kwargs)

    entries: dict[str, dict[str, Any]] = {}
    logs: dict[str, _TeamLog] = {}
    try:
        queued = [team for team in teams if team.id in locks]
        for team in queued:
            log_path = reports_dir / team.id / LOG_FILE
            logs[team.id] = _TeamLog(log_path, team.id, clock, echo)
            entries[team.id] = {
                "state": PENDING,
                "step": None,
                "steps": {name: None for name, _ in steps},
                "started_at": None,
                "finished_at": None,
                "error": None,
                "log": f"{team.id}/{LOG_FILE}",
            }
            if dolos is not None:
                entries[team.id]["steps"][DOLOS_STEP] = None
        status(entries)

        for team in queued:
            entry, log = entries[team.id], logs[team.id]
            entry["started_at"] = _stamp(clock)
            errors: list[str] = []
            for name, step in steps:
                entry["state"] = FETCHING if name == FETCH_STEP else ANALYZING
                entry["step"] = name
                status({team.id: entry})
                try:
                    summary = step(team)
                except STEP_ERRORS as exc:
                    entry["steps"][name] = STEP_ERROR
                    errors.append(f"{name}: {exc}")
                    log.write(f"{name}: error: {exc}")
                    if name == FETCH_STEP:
                        break
                else:
                    entry["steps"][name] = STEP_OK
                    log.write(f"{name}: ok: {summary}")
            entry["step"] = None
            entry["error"] = "; ".join(errors) or None
            if errors and entry["steps"].get(FETCH_STEP) == STEP_ERROR:
                entry["state"] = ERROR
                entry["finished_at"] = _stamp(clock)
            status({team.id: entry})

        if dolos is not None and queued:
            pending = [team.id for team in queued if entries[team.id]["state"] != ERROR]
            for team_id in pending:
                entries[team_id].update(state=ANALYZING, step=DOLOS_STEP)
            status({team_id: entries[team_id] for team_id in pending})
            with _wait_lock(reports_dir / LOCKS_DIR / "dolos.lock"):
                compared = _fetched(config, repos_dir)
                dolos_entry: dict[str, Any] = {"teams": compared, "finished_at": None, "error": None}
                if len(compared) < 2:
                    result.dolos = STEP_SKIPPED
                    message = "omitido: se necesitan al menos dos equipos obtenidos"
                else:
                    try:
                        message = f"ok: {dolos(compared)}"
                        result.dolos = STEP_OK
                    except STEP_ERRORS as exc:
                        result.dolos = STEP_ERROR
                        dolos_entry["error"] = str(exc)
                        message = f"error: {exc}"
                dolos_entry["state"] = result.dolos
                dolos_entry["finished_at"] = _stamp(clock)
            for team_id in pending:
                entries[team_id]["steps"][DOLOS_STEP] = result.dolos
                logs[team_id].write(f"{DOLOS_STEP}: {message}")
                if result.dolos == STEP_ERROR:
                    previous = entries[team_id]["error"]
                    error = f"{DOLOS_STEP}: {dolos_entry['error']}"
                    entries[team_id]["error"] = f"{previous}; {error}" if previous else error
            status({}, dolos=dolos_entry)

        for team in queued:
            entry = entries[team.id]
            if entry["state"] != ERROR:
                entry["state"] = ERROR if entry["error"] else DONE
                entry["step"] = None
                entry["finished_at"] = _stamp(clock)
            (result.failed if entry["state"] == ERROR else result.analyzed).append(team.id)
        status(entries)
    except BaseException:
        interrupted = {
            team_id: {**entry, "state": ERROR, "step": None, "error": "análisis interrumpido"}
            for team_id, entry in entries.items()
            if entry["state"] not in (DONE, ERROR)
        }
        if interrupted:
            try:
                status(interrupted)
            except OSError:
                pass
        raise
    finally:
        for handle in locks.values():
            _unlock(handle)
    return result
