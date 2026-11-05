"""Tests for git history signals (PB-7, SPEC.md "Detección de plagio").

git is never called: a fake runner answers ``rev-parse`` and ``log`` with canned output.
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from acr import git_signals
from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.config import Event
from acr.git_signals import (
    GitSignalsError,
    build_signals,
    read_history,
    write_git_signals,
)

EVENT = Event(
    nombre="Hackathon",
    inicio=datetime.fromisoformat("2026-11-01T09:00:00-06:00"),
    fin=datetime.fromisoformat("2026-11-03T18:00:00-06:00"),
)

# Newest first, as git log prints it.
LOG = "\n".join(
    [
        "c3 2026-11-03T19:00:00-06:00 2026-11-03T19:00:00-06:00",  # after the end
        "c2 2026-11-02T10:00:00-06:00 2026-11-02T10:00:00-06:00",  # inside
        "c1 2026-10-20T10:00:00-06:00 2026-11-01T10:00:00-06:00",  # authored before, rebased inside
    ]
) + "\n"


class FakeGit:
    def __init__(self, log: str = LOG, has_head: bool = True, log_code: int = 0):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.log = log
        self.has_head = has_head
        self.log_code = log_code

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        self.kwargs.append(kwargs)
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0 if self.has_head else 1, stdout="", stderr="")
        if self.log_code:
            return subprocess.CompletedProcess(cmd, self.log_code, stdout="", stderr="fatal: bad object")
        return subprocess.CompletedProcess(cmd, 0, stdout=self.log, stderr="")


def make_repo(root: Path, team_id: str = "equipo-01", git: bool = True) -> Path:
    repo = root / "repos" / team_id
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("hola", encoding="utf-8")
    if git:
        (repo / ".git").mkdir()
    return repo


# --- reading the history -------------------------------------------------------------------


def test_read_history_runs_only_safe_read_commands(tmp_path):
    repo = make_repo(tmp_path)
    fake = FakeGit()
    commits = read_history(repo, runner=fake)
    assert [c.hash for c in commits] == ["c3", "c2", "c1"]
    for cmd, kwargs in zip(fake.calls, fake.kwargs, strict=True):
        assert cmd[0] == "git"
        assert f"--git-dir={repo / '.git'}" in cmd
        assert "core.fsmonitor=false" in cmd and "log.showSignature=false" in cmd
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs.get("shell") is not True
    assert "rev-parse" in fake.calls[0]
    assert "log" in fake.calls[1] and fake.calls[1][-1] == "HEAD"


def test_no_git_folder_means_no_history(tmp_path):
    repo = make_repo(tmp_path, git=False)
    fake = FakeGit()
    assert read_history(repo, runner=fake) is None
    assert fake.calls == []


def test_git_file_link_is_not_followed(tmp_path):
    repo = make_repo(tmp_path, git=False)
    (repo / ".git").write_text("gitdir: C:/somewhere/else", encoding="utf-8")
    fake = FakeGit()
    assert read_history(repo, runner=fake) is None
    assert fake.calls == []


def test_repo_without_commits(tmp_path):
    repo = make_repo(tmp_path)
    fake = FakeGit(has_head=False)
    assert read_history(repo, runner=fake) == []
    assert len(fake.calls) == 1


def test_git_log_failure(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(GitSignalsError, match="bad object"):
        read_history(repo, runner=FakeGit(log_code=128))


def test_unexpected_log_output(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(GitSignalsError):
        read_history(repo, runner=FakeGit(log="c1 not-a-date not-a-date\n"))


def test_git_not_installed(tmp_path):
    repo = make_repo(tmp_path)

    def missing(cmd, **kwargs):
        raise FileNotFoundError("git")

    with pytest.raises(GitSignalsError, match="no está instalado"):
        read_history(repo, runner=missing)


# --- building the signals ------------------------------------------------------------------


def test_signals_first_last_count_and_outside(tmp_path):
    repo = make_repo(tmp_path)
    signals = build_signals("equipo-01", EVENT, read_history(repo, runner=FakeGit()))
    assert signals["has_git"] is True
    assert signals["commit_count"] == 3
    assert signals["single_commit"] is False
    assert signals["first_commit"]["hash"] == "c1"
    assert signals["last_commit"]["hash"] == "c3"
    outside = signals["outside_event"]
    assert outside["before_start"] == 1
    assert outside["after_end"] == 1
    assert [(c["hash"], c["reasons"]) for c in outside["commits"]] == [
        ("c1", ["before_start"]),
        ("c3", ["after_end"]),
    ]
    assert signals["event"] == {"start": "2026-11-01T09:00:00-06:00", "end": "2026-11-03T18:00:00-06:00"}


def test_dates_compared_across_time_zones():
    commits = read_history_from("c1 2026-11-01T14:30:00+00:00 2026-11-01T14:30:00+00:00\n")
    signals = build_signals("equipo-01", EVENT, commits)  # 14:30Z = 08:30-06:00, before 09:00
    assert signals["outside_event"]["before_start"] == 1
    assert signals["single_commit"] is True


def test_event_without_time_zone_is_local_time():
    naive = Event(nombre="H", inicio=datetime(2026, 11, 1, 9), fin=datetime(2026, 11, 3, 18))
    local = datetime(2026, 11, 2, 12).astimezone().isoformat()
    signals = build_signals("equipo-01", naive, read_history_from(f"c1 {local} {local}\n"))
    assert signals["outside_event"]["commits"] == []


def test_signals_without_git_or_commits():
    for commits, has_git in ((None, False), ([], True)):
        signals = build_signals("equipo-01", EVENT, commits)
        assert signals["has_git"] is has_git
        assert signals["commit_count"] == 0
        assert signals["first_commit"] is None and signals["last_commit"] is None
        assert signals["outside_event"] == {"before_start": 0, "after_end": 0, "commits": []}


def read_history_from(log: str):
    return git_signals._parse_log(log)


# --- writing the report --------------------------------------------------------------------


def test_write_git_signals(tmp_path):
    make_repo(tmp_path)
    path, signals = write_git_signals(
        "equipo-01", EVENT, tmp_path / "repos", tmp_path / "reports", runner=FakeGit()
    )
    assert path == tmp_path / "reports" / "equipo-01" / "git-signals.json"
    assert json.loads(path.read_text(encoding="utf-8")) == signals
    assert not list(path.parent.glob(".*.tmp"))


def test_write_requires_fetched_repo(tmp_path):
    with pytest.raises(GitSignalsError, match="acr fetch"):
        write_git_signals("equipo-01", EVENT, tmp_path / "repos", tmp_path / "reports", runner=FakeGit())


@pytest.mark.parametrize("team_id", ["../x", "A", ""])
def test_write_rejects_invalid_id(tmp_path, team_id):
    with pytest.raises(GitSignalsError, match="id inválido"):
        write_git_signals(team_id, EVENT, tmp_path / "repos", tmp_path / "reports", runner=FakeGit())


# --- CLI -----------------------------------------------------------------------------------


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


def test_cli_git_signals_all(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(git_signals.subprocess, "run", FakeGit())
    make_repo(tmp_path, "equipo-01")
    make_repo(tmp_path, "equipo-02", git=False)
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "git-signals"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "equipo-01: 3 commit(s), 1 antes del inicio, 1 después del cierre" in out
    assert "equipo-02: sin historial de git" in out
    assert (tmp_path / "reports" / "equipo-02" / "git-signals.json").is_file()


def test_cli_git_signals_one_team_and_continue_on_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(git_signals.subprocess, "run", FakeGit())
    make_repo(tmp_path, "equipo-02")
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "git-signals", "equipo-02"]) == EXIT_OK
    assert main(["--config", str(config), "git-signals"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "equipo-01: error" in err and "1 de 2" in err
    assert (tmp_path / "reports" / "equipo-02" / "git-signals.json").is_file()


def test_cli_git_signals_unknown_team_and_bad_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = write_config(tmp_path, ["equipo-01"])
    assert main(["--config", str(config), "git-signals", "otro"]) == EXIT_ERROR
    assert main(["--config", str(tmp_path / "nope.yml"), "git-signals"]) == EXIT_ERROR
