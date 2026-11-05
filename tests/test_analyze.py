"""Tests for ``acr analyze`` (PB-12). No network and no external tools: every step is faked."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from acr import analyze
from acr.analyze import (
    AnalysisResult,
    Options,
    _try_lock,
    _unlock,
    default_steps,
    is_team_busy,
    read_status,
    run_analysis,
    team_lock_path,
    update_status,
)
from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.config import Config, Event, Team
from acr.dolos import DolosError
from acr.fetch import FetchError, FetchResult
from acr.megalinter import MegaLinterResult
from acr.scanoss import ScanossError

EVENT = Event(
    nombre="H",
    inicio=datetime(2026, 11, 1, 9, tzinfo=UTC),
    fin=datetime(2026, 11, 3, 18, tzinfo=UTC),
)


def make_team(team_id: str) -> Team:
    return Team(id=team_id, nombre=f"N {team_id}", proyecto="P", objetivo="O", repo="https://example.org/r.git")


def make_config(*ids: str) -> Config:
    return Config(evento=EVENT, equipos=tuple(make_team(i) for i in ids))


def clock() -> datetime:
    return datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


class Recorder:
    """Fake steps that record their calls (and the status seen at each call)."""

    def __init__(self, tmp_path: Path, fail: dict[str, Exception] | None = None):
        self.repos = tmp_path / "repos"
        self.reports = tmp_path / "reports"
        self.fail = fail or {}
        self.calls: list[tuple[str, str]] = []
        self.seen_states: list[str] = []

    def step(self, name: str):
        def run(team: Team) -> str:
            self.calls.append((name, team.id))
            status = read_status(self.reports)
            entry = next(e for e in status["teams"] if e["id"] == team.id)
            self.seen_states.append(f"{name}:{entry['state']}:{entry['step']}")
            key = f"{name}:{team.id}"
            if key in self.fail:
                raise self.fail[key]
            if name == "fetch":
                (self.repos / team.id).mkdir(parents=True, exist_ok=True)
            return f"{name} done"

        return run

    def steps(self):
        return [(name, self.step(name)) for name in ("fetch", "git-signals", "scc", "megalinter", "scanoss")]

    def dolos(self, team_ids: list[str]) -> str:
        self.calls.append(("dolos", ",".join(team_ids)))
        if "dolos" in self.fail:
            raise self.fail["dolos"]
        return "dolos done"

    def run(self, config: Config, teams=None, **kwargs) -> AnalysisResult:
        return run_analysis(
            config,
            config.equipos if teams is None else teams,
            steps=self.steps(),
            dolos=self.dolos,
            repos_dir=self.repos,
            reports_dir=self.reports,
            clock=clock,
            echo=lambda _: None,
            warn=lambda _: None,
            **kwargs,
        )


def entries(reports: Path) -> dict[str, dict]:
    return {entry["id"]: entry for entry in read_status(reports)["teams"]}


def test_runs_steps_in_order_for_every_team_then_dolos_once(tmp_path):
    rec = Recorder(tmp_path)
    config = make_config("equipo-01", "equipo-02")
    result = rec.run(config)

    per_team = ["fetch", "git-signals", "scc", "megalinter", "scanoss"]
    assert rec.calls == (
        [(name, "equipo-01") for name in per_team]
        + [(name, "equipo-02") for name in per_team]
        + [("dolos", "equipo-01,equipo-02")]
    )
    assert result.analyzed == ["equipo-01", "equipo-02"] and result.ok
    assert rec.seen_states[0] == "fetch:obteniendo:fetch"
    assert rec.seen_states[1] == "git-signals:analizando:git-signals"

    status = read_status(rec.reports)
    assert status["schema_version"] == 1
    assert status["dolos"]["state"] == "ok"
    assert status["dolos"]["teams"] == ["equipo-01", "equipo-02"]
    team = entries(rec.reports)["equipo-01"]
    assert team["state"] == "listo" and team["step"] is None and team["error"] is None
    assert team["steps"] == {
        "fetch": "ok", "git-signals": "ok", "scc": "ok", "megalinter": "ok", "scanoss": "ok", "dolos": "ok"
    }
    assert team["started_at"] == team["finished_at"] == "2026-11-04T10:00:00+00:00"
    assert team["nombre"] == "N equipo-01"
    assert team["log"] == "equipo-01/analyze.log"
    log = (rec.reports / "equipo-01" / "analyze.log").read_text(encoding="utf-8")
    assert "fetch: ok: fetch done" in log and "dolos: ok: dolos done" in log


def test_single_team_lists_others_as_pending_and_compares_all_fetched(tmp_path):
    rec = Recorder(tmp_path)
    config = make_config("equipo-01", "equipo-02", "equipo-03")
    (rec.repos / "equipo-03").mkdir(parents=True)
    result = rec.run(config, teams=[config.equipos[0]])

    assert {team for _, team in rec.calls if _ != "dolos"} == {"equipo-01"}
    assert ("dolos", "equipo-01,equipo-03") in rec.calls
    assert result.analyzed == ["equipo-01"]
    states = {team_id: entry["state"] for team_id, entry in entries(rec.reports).items()}
    assert states == {"equipo-01": "listo", "equipo-02": "pendiente", "equipo-03": "pendiente"}


def test_previous_entries_are_kept_and_removed_teams_dropped(tmp_path):
    rec = Recorder(tmp_path)
    rec.run(make_config("equipo-01", "equipo-02", "equipo-09"))
    config = make_config("equipo-01", "equipo-02")
    rec.run(config, teams=[config.equipos[1]])
    states = {team_id: entry["state"] for team_id, entry in entries(rec.reports).items()}
    assert states == {"equipo-01": "listo", "equipo-02": "listo"}


def test_fetch_failure_stops_that_team_only(tmp_path):
    rec = Recorder(tmp_path, fail={"fetch:equipo-01": FetchError("repo no accesible")})
    result = rec.run(make_config("equipo-01", "equipo-02", "equipo-03"))

    assert [call for call in rec.calls if call[1] == "equipo-01"] == [("fetch", "equipo-01")]
    assert ("dolos", "equipo-02,equipo-03") in rec.calls
    assert result.failed == ["equipo-01"] and result.analyzed == ["equipo-02", "equipo-03"]
    assert not result.ok
    team = entries(rec.reports)["equipo-01"]
    assert team["state"] == "error"
    assert team["error"] == "fetch: repo no accesible"
    steps = team["steps"]
    assert steps["fetch"] == "error" and steps["scc"] is None and steps["dolos"] is None
    assert "fetch: error: repo no accesible" in (rec.reports / "equipo-01" / "analyze.log").read_text("utf-8")


def test_later_step_failure_keeps_running_the_other_steps(tmp_path):
    rec = Recorder(tmp_path, fail={"scanoss:equipo-01": ScanossError("límite de consultas")})
    result = rec.run(make_config("equipo-01", "equipo-02"))

    assert ("scanoss", "equipo-01") in rec.calls and ("dolos", "equipo-01,equipo-02") in rec.calls
    assert result.failed == ["equipo-01"] and result.analyzed == ["equipo-02"]
    team = entries(rec.reports)["equipo-01"]
    assert team["state"] == "error" and team["error"] == "scanoss: límite de consultas"
    assert team["steps"]["scanoss"] == "error" and team["steps"]["dolos"] == "ok"
    assert team["finished_at"] is not None


def test_dolos_failure_marks_analyzed_teams(tmp_path):
    rec = Recorder(tmp_path, fail={"dolos": DolosError("Dolos (python) falló")})
    result = rec.run(make_config("equipo-01", "equipo-02"))
    assert result.dolos == "error" and not result.ok
    status = read_status(rec.reports)
    assert status["dolos"]["state"] == "error" and status["dolos"]["error"] == "Dolos (python) falló"
    team = entries(rec.reports)["equipo-01"]
    assert team["state"] == "error" and team["error"] == "dolos: Dolos (python) falló"


def test_dolos_skipped_with_fewer_than_two_fetched_teams(tmp_path):
    rec = Recorder(tmp_path)
    config = make_config("equipo-01", "equipo-02")
    result = rec.run(config, teams=[config.equipos[0]])
    assert not any(name == "dolos" for name, _ in rec.calls)
    assert result.dolos == "omitido" and result.ok
    team = entries(rec.reports)["equipo-01"]
    assert team["state"] == "listo" and team["steps"]["dolos"] == "omitido"
    assert read_status(rec.reports)["dolos"]["state"] == "omitido"


def test_team_already_being_analyzed_is_not_started_again(tmp_path):
    rec = Recorder(tmp_path)
    config = make_config("equipo-01", "equipo-02")
    held = _try_lock(team_lock_path(rec.reports, "equipo-01"))
    assert held is not None
    try:
        assert is_team_busy(rec.reports, "equipo-01")
        result = rec.run(config)
    finally:
        _unlock(held)

    assert result.busy == ["equipo-01"] and not result.ok
    assert all(team != "equipo-01" for name, team in rec.calls if name != "dolos")
    assert not is_team_busy(rec.reports, "equipo-01")
    assert not is_team_busy(rec.reports, "equipo-02")  # released after the run


def test_lock_is_held_during_the_run(tmp_path):
    rec = Recorder(tmp_path)
    busy_during_run = []

    def check(team: Team) -> str:
        busy_during_run.append(is_team_busy(rec.reports, team.id))
        return "ok"

    config = make_config("equipo-01")
    run_analysis(
        config,
        config.equipos,
        steps=[("scc", check)],
        dolos=None,
        repos_dir=rec.repos,
        reports_dir=rec.reports,
        clock=clock,
        echo=lambda _: None,
    )
    assert busy_during_run == [True]
    assert entries(rec.reports)["equipo-01"]["steps"] == {"scc": "ok"}


def test_interrupted_run_marks_error_and_releases_lock(tmp_path):
    rec = Recorder(tmp_path)

    def boom(team: Team) -> str:
        raise KeyboardInterrupt

    config = make_config("equipo-01", "equipo-02")
    with pytest.raises(KeyboardInterrupt):
        run_analysis(
            config,
            config.equipos,
            steps=[("scc", boom)],
            dolos=None,
            repos_dir=rec.repos,
            reports_dir=rec.reports,
            clock=clock,
            echo=lambda _: None,
        )
    for team in entries(rec.reports).values():
        assert team["state"] == "error" and team["error"] == "análisis interrumpido"
    assert not is_team_busy(rec.reports, "equipo-01")


def test_stale_running_state_becomes_error(tmp_path):
    reports = tmp_path / "reports"
    config = make_config("equipo-01", "equipo-02")
    update_status(config, reports, {"equipo-01": {"state": "analizando", "step": "scc"}}, clock=clock)
    # Nobody holds the lock of equipo-01 any more (the process died): the next write fixes it.
    status = update_status(config, reports, clock=clock)
    team = status["teams"][0]
    assert team["state"] == "error" and team["error"] == "análisis interrumpido"
    assert status["teams"][1]["state"] == "pendiente"


def test_running_state_kept_while_lock_is_held(tmp_path):
    reports = tmp_path / "reports"
    config = make_config("equipo-01")
    held = _try_lock(team_lock_path(reports, "equipo-01"))
    try:
        update_status(config, reports, {"equipo-01": {"state": "analizando"}}, clock=clock)
        status = update_status(config, reports, clock=clock)
    finally:
        _unlock(held)
    assert status["teams"][0]["state"] == "analizando"


def test_unreadable_status_is_rebuilt(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "status.json").write_text("{no es json", encoding="utf-8")
    assert read_status(reports) is None
    status = update_status(make_config("equipo-01"), reports, clock=clock)
    assert status["teams"][0]["state"] == "pendiente"
    assert json.loads((reports / "status.json").read_text(encoding="utf-8")) == status


@pytest.mark.parametrize("team_id", ["../x", "A", ""])
def test_lock_path_rejects_invalid_id(tmp_path, team_id):
    with pytest.raises(ValueError):
        team_lock_path(tmp_path, team_id)


# --- default steps and CLI ---------------------------------------------------------------------


@pytest.fixture
def fake_tools(monkeypatch):
    """Replace every tool wrapper used by the default steps; return the list of calls."""
    calls: list[str] = []

    def fetch_team(team, repos_dir, *, max_zip_bytes):
        calls.append(f"fetch:{team.id}:{max_zip_bytes}")
        (repos_dir / team.id).mkdir(parents=True, exist_ok=True)
        return FetchResult(team_id=team.id, kind="git", path=repos_dir / team.id)

    def write_git_signals(team_id, event, repos_dir, reports_dir):
        calls.append(f"git-signals:{team_id}")
        return reports_dir / team_id / "git-signals.json", {
            "has_git": True, "commit_count": 3, "outside_event": {"before_start": 0, "after_end": 1}
        }

    def write_scc(team_id, repos_dir, reports_dir):
        calls.append(f"scc:{team_id}")
        return reports_dir / team_id / "scc.json", {"totals": {"languages": 2, "code": 120}}

    def run_megalinter(team_id, repos_dir, reports_dir, *, engine, image, timeout):
        calls.append(f"megalinter:{team_id}:{engine}:{image}:{timeout}")
        return MegaLinterResult(returncode=1, files=[Path("megalinter.sarif")], missing=["trivy.sarif"])

    def write_scanoss(team_id, repos_dir, reports_dir):
        calls.append(f"scanoss:{team_id}")
        return reports_dir / team_id / "scanoss.json", {"totals": {"files_matched": 1, "files_scanned": 9}}

    class DolosResult:
        pairs_path = Path("reports/dolos/pairs.csv")
        summary = {"totals": {"team_pairs_with_matches": 1, "team_pairs": 1}}

    def write_dolos(team_ids, repos_dir, reports_dir):
        calls.append(f"dolos:{','.join(team_ids)}")
        return DolosResult()

    for name, fake in {
        "fetch_team": fetch_team,
        "write_git_signals": write_git_signals,
        "write_scc": write_scc,
        "run_megalinter": run_megalinter,
        "write_scanoss": write_scanoss,
        "write_dolos": write_dolos,
    }.items():
        monkeypatch.setattr(analyze, name, fake)
    return calls


def test_default_steps_order_and_summaries(tmp_path, fake_tools):
    options = Options(repos_dir=tmp_path / "repos", reports_dir=tmp_path / "reports", engine="podman")
    steps = default_steps(make_config("equipo-01"), options)
    assert [name for name, _ in steps] == ["fetch", "git-signals", "scc", "megalinter", "scanoss"]
    team = make_team("equipo-01")
    summaries = [step(team) for _, step in steps]
    assert "1 después del cierre" in summaries[1]
    assert "2 lenguaje(s), 120 línea(s)" in summaries[2]
    assert "con errores de linters" in summaries[3] and "trivy.sarif" in summaries[3]
    assert "1 de 9 archivo(s)" in summaries[4]
    assert fake_tools[3] == "megalinter:equipo-01:podman:docker.io/oxsecurity/megalinter:v10:3600"


def write_config(tmp_path: Path, ids: list[str]) -> Path:
    data = {
        "evento": {"nombre": "H", "inicio": "2026-11-01T09:00:00-06:00", "fin": "2026-11-03T18:00:00-06:00"},
        "equipos": [
            {"id": i, "nombre": "N", "proyecto": "P", "objetivo": "O", "repo": "https://example.org/r.git"}
            for i in ids
        ],
    }
    path = tmp_path / "equipos.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_cli_analyze_all(tmp_path, monkeypatch, capsys, fake_tools):
    monkeypatch.chdir(tmp_path)
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    args = ["--config", str(config), "analyze", "--engine", "docker"]
    args += ["--timeout-min", "5", "--max-zip-mb", "2"]
    assert main(args) == EXIT_OK
    out = capsys.readouterr().out
    assert "equipo-01: scc: ok:" in out
    assert "2 equipo(s) listo(s), 0 con error, 0 ya en curso -> reports/status.json" in out
    assert "fetch:equipo-01:2097152" in fake_tools
    assert "megalinter:equipo-02:docker:docker.io/oxsecurity/megalinter:v10:300" in fake_tools
    assert fake_tools[-1] == "dolos:equipo-01,equipo-02"
    states = [team["state"] for team in read_status(tmp_path / "reports")["teams"]]
    assert states == ["listo", "listo"]


def test_cli_analyze_one_team_and_errors(tmp_path, monkeypatch, capsys, fake_tools):
    monkeypatch.chdir(tmp_path)
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "analyze", "equipo-02"]) == EXIT_OK
    assert all("equipo-01" not in call for call in fake_tools)
    assert main(["--config", str(config), "analyze", "equipo-09"]) == EXIT_ERROR
    assert "no hay un equipo con id 'equipo-09'" in capsys.readouterr().err
    assert main(["--config", str(tmp_path / "nope.yml"), "analyze"]) == EXIT_ERROR


def test_cli_analyze_reports_busy_team(tmp_path, monkeypatch, capsys, fake_tools):
    monkeypatch.chdir(tmp_path)
    config = write_config(tmp_path, ["equipo-01"])
    held = _try_lock(team_lock_path(Path("reports"), "equipo-01"))
    try:
        assert main(["--config", str(config), "analyze"]) == EXIT_ERROR
    finally:
        _unlock(held)
    captured = capsys.readouterr()
    assert "ya hay un análisis en curso" in captured.err
    assert "0 equipo(s) listo(s), 0 con error, 1 ya en curso" in captured.out
    assert fake_tools == []
