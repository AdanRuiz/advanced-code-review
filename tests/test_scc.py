"""Tests for scc language and size recognition (PB-8, SPEC.md "Stack de componentes", paso 1).

scc is never called: a fake runner answers with canned JSON output.
"""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from acr import scc
from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.scc import SccError, build_report, run_scc, write_scc

SCC_OUTPUT = [
    {
        "Name": "Python", "Bytes": 4000, "CodeBytes": 0, "Lines": 120, "Code": 90, "Comment": 10,
        "Blank": 20, "Complexity": 7, "Count": 3, "WeightedComplexity": 0, "Files": [], "ULOC": 0,
    },
    {
        "Name": "TypeScript", "Bytes": 9000, "CodeBytes": 0, "Lines": 300, "Code": 250, "Comment": 5,
        "Blank": 45, "Complexity": 30, "Count": 6, "WeightedComplexity": 0, "Files": [], "ULOC": 0,
    },
    {
        "Name": "Markdown", "Bytes": 500, "CodeBytes": 0, "Lines": 20, "Code": 15, "Comment": 0,
        "Blank": 5, "Complexity": 0, "Count": 1, "WeightedComplexity": 0, "Files": None, "ULOC": 0,
    },
]


class FakeScc:
    def __init__(self, stdout: str | None = None, code: int = 0, stderr: str = ""):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.stdout = json.dumps(SCC_OUTPUT) if stdout is None else stdout
        self.code = code
        self.stderr = stderr

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        self.kwargs.append(kwargs)
        return subprocess.CompletedProcess(cmd, self.code, stdout=self.stdout, stderr=self.stderr)


def make_repo(root: Path, team_id: str = "equipo-01") -> Path:
    repo = root / "repos" / team_id
    repo.mkdir(parents=True)
    (repo / "main.py").write_text("print('hola')\n", encoding="utf-8")
    return repo


# --- running scc ---------------------------------------------------------------------------


def test_run_scc_uses_argument_list_and_json_format(tmp_path):
    repo = make_repo(tmp_path)
    fake = FakeScc()
    assert run_scc(repo, runner=fake) == SCC_OUTPUT
    (cmd,), (kwargs,) = fake.calls, fake.kwargs
    assert cmd == ["scc", "--format", "json", str(repo)]
    assert kwargs.get("shell") is not True
    assert kwargs["timeout"] == scc.SCC_TIMEOUT_SECONDS
    assert kwargs["capture_output"] is True


def test_scc_not_installed(tmp_path):
    def missing(cmd, **kwargs):
        raise FileNotFoundError("scc")

    with pytest.raises(SccError, match="no está instalado"):
        run_scc(make_repo(tmp_path), runner=missing)


def test_scc_timeout(tmp_path):
    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    with pytest.raises(SccError, match="tardó"):
        run_scc(make_repo(tmp_path), runner=slow)


def test_scc_failure_shows_last_stderr_line(tmp_path):
    fake = FakeScc(stdout="", code=1, stderr="warning\nerror: permission denied\n")
    with pytest.raises(SccError, match="permission denied"):
        run_scc(make_repo(tmp_path), runner=fake)


@pytest.mark.parametrize("stdout", ["not json", '{"Name": "Python"}', "[1, 2]"])
def test_unexpected_scc_output(tmp_path, stdout):
    with pytest.raises(SccError):
        run_scc(make_repo(tmp_path), runner=FakeScc(stdout=stdout))


@pytest.mark.parametrize("stdout", ["null", "[]", ""])
def test_empty_repo_has_no_languages(tmp_path, stdout):
    assert run_scc(make_repo(tmp_path), runner=FakeScc(stdout=stdout)) == []


# --- building the report -------------------------------------------------------------------


def test_report_has_languages_lines_and_files():
    report = build_report("equipo-01", SCC_OUTPUT)
    assert report["schema_version"] == 1
    assert report["team_id"] == "equipo-01"
    assert [lang["name"] for lang in report["languages"]] == ["TypeScript", "Python", "Markdown"]
    assert report["languages"][1] == {
        "name": "Python", "files": 3, "lines": 120, "code": 90, "comment": 10, "blank": 20,
        "bytes": 4000, "complexity": 7,
    }
    assert report["totals"] == {
        "languages": 3, "files": 10, "lines": 440, "code": 355, "comment": 15, "blank": 70,
        "bytes": 13500, "complexity": 37,
    }


def test_report_for_empty_repo():
    report = build_report("equipo-01", [])
    assert report["languages"] == []
    assert report["totals"]["languages"] == 0 and report["totals"]["files"] == 0


@pytest.mark.parametrize(
    "item",
    [
        {"Name": "", "Count": 1},
        {"Count": 1},
        {"Name": "Go", "Count": -1},
        {"Name": "Go", "Lines": "12"},
        {"Name": "Go", "Code": True},
    ],
)
def test_report_rejects_invalid_language(item):
    with pytest.raises(SccError, match="salida inesperada"):
        build_report("equipo-01", [item])


# --- writing the report --------------------------------------------------------------------


def test_write_scc(tmp_path):
    make_repo(tmp_path)
    path, report = write_scc("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=FakeScc())
    assert path == tmp_path / "reports" / "equipo-01" / "scc.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert not list(path.parent.glob(".*.tmp"))


def test_write_requires_fetched_repo(tmp_path):
    fake = FakeScc()
    with pytest.raises(SccError, match="acr fetch"):
        write_scc("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=fake)
    assert fake.calls == []


@pytest.mark.parametrize("team_id", ["../x", "A", ""])
def test_write_rejects_invalid_id(tmp_path, team_id):
    fake = FakeScc()
    with pytest.raises(SccError, match="id inválido"):
        write_scc(team_id, tmp_path / "repos", tmp_path / "reports", runner=fake)
    assert fake.calls == []


def test_failed_scc_keeps_previous_report(tmp_path):
    make_repo(tmp_path)
    path, report = write_scc("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=FakeScc())
    with pytest.raises(SccError):
        write_scc("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=FakeScc(code=2))
    assert json.loads(path.read_text(encoding="utf-8")) == report


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


def test_cli_scc_all(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scc.subprocess, "run", FakeScc())
    make_repo(tmp_path, "equipo-01")
    make_repo(tmp_path, "equipo-02")
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "scc"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "equipo-01: 3 lenguaje(s) (principal: TypeScript), 10 archivo(s), 355 línea(s) de código" in out
    for team_id in ("equipo-01", "equipo-02"):
        assert (tmp_path / "reports" / team_id / "scc.json").is_file()


def test_cli_scc_one_team_and_continue_on_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scc.subprocess, "run", FakeScc())
    make_repo(tmp_path, "equipo-02")
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "scc", "equipo-02"]) == EXIT_OK
    assert main(["--config", str(config), "scc"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "equipo-01: error" in err and "1 de 2" in err
    assert (tmp_path / "reports" / "equipo-02" / "scc.json").is_file()


def test_cli_scc_unknown_team_and_bad_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = write_config(tmp_path, ["equipo-01"])
    assert main(["--config", str(config), "scc", "otro"]) == EXIT_ERROR
    assert main(["--config", str(tmp_path / "nope.yml"), "scc"]) == EXIT_ERROR
