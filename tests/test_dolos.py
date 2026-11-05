"""Tests for Dolos (PB-11, SPEC.md "Detección de plagio").

Dolos is never called: a fake runner reads the info CSV it receives and writes a ``pairs.csv``
like Dolos does (one row per pair of files with shared fragments), including pairs of the same
team, which must be ignored.
"""

import csv
import io
import json
import os
import subprocess
from itertools import combinations
from pathlib import Path

import pytest
import yaml

from acr import dolos
from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.dolos import DolosError, FilePair, collect_files, parse_pairs, team_pairs, write_dolos

COPIED = "def total(items):\n    return sum(i.price for i in items)\n"
DOLOS_HEADER = (
    "id,leftFileId,leftFilePath,rightFileId,rightFilePath,similarity,longestFragment,totalOverlap\n"
)


class FakeDolos:
    """Writes a pairs.csv: identical files score 1.0, files sharing the first line 0.5."""

    def __init__(self, fail: bool = False, relative_paths: bool = False, write_output: bool = True):
        self.fail = fail
        self.relative_paths = relative_paths
        self.write_output = write_output
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.infos: list[list[dict]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        self.kwargs.append(kwargs)
        if self.fail:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="trace\nError: unsupported\n")
        info = Path(cmd[-1])
        rows = list(csv.DictReader(io.StringIO(info.read_text(encoding="utf-8"))))
        self.infos.append(rows)
        if not self.write_output:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        files = []
        for row in rows:
            path = Path(os.path.normpath(info.parent / row["filename"]))
            shown = os.path.relpath(path, kwargs["cwd"]) if self.relative_paths else str(path)
            files.append((shown, path.read_text(encoding="utf-8")))
        lines = [DOLOS_HEADER]
        for n, ((left, a), (right, b)) in enumerate(combinations(files, 2)):
            if a == b:
                lines.append(f"{n},0,{left},1,{right},1,12,40\n")
            elif a.splitlines()[0] == b.splitlines()[0]:
                lines.append(f"{n},0,{left},1,{right},0.5,3,9\n")
        output = Path(cmd[cmd.index("--output-destination") + 1])
        output.mkdir(parents=True)
        (output / "pairs.csv").write_text("".join(lines), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def write_file(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repos(root: Path) -> Path:
    repos = root / "repos"
    write_file(repos / "equipo-01", "app/cart.py", COPIED)
    write_file(repos / "equipo-01", "app/main.py", "print('a')\n")
    write_file(repos / "equipo-01", "web/index.js", "console.log(1)\n")
    write_file(repos / "equipo-01", "README.md", "# Equipo 1\n")
    write_file(repos / "equipo-02", "src/cart.py", COPIED)
    write_file(repos / "equipo-02", "src/copy.py", COPIED)  # same-team pair: ignored
    write_file(repos / "equipo-02", "src/other.py", "def total(items):\n    return 0\n")
    write_file(repos / "equipo-02", "node_modules/x/index.js", "console.log(1)\n")
    write_file(repos / "equipo-02", ".git/HEAD", "ref: refs/heads/main\n")
    write_file(repos / "equipo-03", "Main.java", "class Main {}\n")
    write_file(repos / "equipo-03", "Dockerfile", "FROM scratch\n")
    return repos


def read_csv(path: Path) -> list[dict]:
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))


# --- file collection -------------------------------------------------------------------------


def test_collect_files_groups_by_language_and_skips_dependencies(tmp_path):
    repos = make_repos(tmp_path)
    sources = collect_files({t: repos / t for t in ("equipo-01", "equipo-02", "equipo-03")})
    assert sources.by_language["python"] == {
        "equipo-01": ["app/cart.py", "app/main.py"],
        "equipo-02": ["src/cart.py", "src/copy.py", "src/other.py"],
    }
    assert sources.by_language["javascript"] == {"equipo-01": ["web/index.js"]}
    assert sources.by_language["java"] == {"equipo-03": ["Main.java"]}
    assert sources.excluded_dependency_files == 1
    assert sources.unsupported == {".md": 1, "(sin extensión)": 1}


# --- Dolos output ----------------------------------------------------------------------------


def test_parse_pairs_maps_files_to_teams_and_drops_same_team(tmp_path):
    repos = {"equipo-01": tmp_path / "repos" / "equipo-01", "equipo-02": tmp_path / "repos" / "equipo-02"}
    work = tmp_path / "work"
    a = repos["equipo-02"] / "x.py"
    b = repos["equipo-01"] / "sub" / "y.py"
    c = repos["equipo-01"] / "z.py"
    text = DOLOS_HEADER + f"0,0,{a},1,{b},0.75,8,20\n1,0,{b},1,{c},0.9,1,1\n"
    assert parse_pairs(text, "python", work, repos) == [
        FilePair("equipo-01", "sub/y.py", "equipo-02", "x.py", "python", 0.75, 8, 20)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "id,left,right\n0,a,b\n",
        DOLOS_HEADER + "0,0,/elsewhere/a.py,1,/elsewhere/b.py,0.5,1,1\n",
    ],
)
def test_parse_pairs_rejects_unexpected_output(tmp_path, text):
    repos = {"equipo-01": tmp_path / "equipo-01"}
    with pytest.raises(DolosError, match="salida inesperada"):
        parse_pairs(text, "python", tmp_path, repos)


def test_team_pairs_lists_every_pair_with_highest_similarity():
    pairs = [
        FilePair("a", "x.py", "b", "y.py", "python", 0.5, 3, 9),
        FilePair("a", "z.py", "b", "y.py", "python", 0.91234, 7, 30),
    ]
    rows = team_pairs(["c", "b", "a"], pairs)
    assert rows[0] == {
        "team_a": "a",
        "team_b": "b",
        "similarity": 0.9123,
        "file_pairs": 2,
        "longest_fragment": 7,
        "total_overlap": 39,
        "file_a": "z.py",
        "file_b": "y.py",
    }
    rest = [(r["team_a"], r["team_b"], r["similarity"]) for r in rows[1:]]
    assert rest == [("a", "c", 0.0), ("b", "c", 0.0)]


# --- write_dolos -----------------------------------------------------------------------------


@pytest.mark.parametrize("relative_paths", [False, True])
def test_write_dolos_runs_once_per_shared_language(tmp_path, relative_paths):
    repos = make_repos(tmp_path)
    fake = FakeDolos(relative_paths=relative_paths)
    result = write_dolos(["equipo-03", "equipo-02", "equipo-01"], repos, tmp_path / "reports", runner=fake)

    # Only Python is used by two teams; JavaScript (one team, the other in node_modules) and Java
    # (one team) are not compared.
    assert len(fake.calls) == 1
    cmd = fake.calls[0]
    assert cmd[1:7] == ["run", "--language", "python", "--output-format", "csv", "--output-destination"]
    assert "serve" not in cmd and "--host" not in cmd
    assert fake.kwargs[0]["timeout"] == dolos.TIMEOUT_SECONDS
    assert sorted(row["label"] for row in fake.infos[0]) == ["equipo-01"] * 2 + ["equipo-02"] * 3

    out = tmp_path / "reports" / "dolos"
    assert result.pairs_path == out / "pairs.csv"
    rows = read_csv(out / "pairs.csv")
    assert list(rows[0]) == list(dolos.PAIR_COLUMNS)
    assert rows[0] == {
        "team_a": "equipo-01",
        "team_b": "equipo-02",
        "similarity": "1.0",
        "file_pairs": "3",
        "longest_fragment": "12",
        "total_overlap": "89",
        "file_a": "app/cart.py",
        "file_b": "src/cart.py",
    }
    assert {(r["team_a"], r["team_b"], r["similarity"]) for r in rows[1:]} == {
        ("equipo-01", "equipo-03", "0.0"),
        ("equipo-02", "equipo-03", "0.0"),
    }

    file_rows = read_csv(out / "file-pairs.csv")
    assert len(file_rows) == 3 and all(r["team_a"] != r["team_b"] for r in file_rows)
    assert {(r["file_a"], r["file_b"], r["similarity"]) for r in file_rows} == {
        ("app/cart.py", "src/cart.py", "1.0"),
        ("app/cart.py", "src/copy.py", "1.0"),
        ("app/cart.py", "src/other.py", "0.5"),
    }

    summary = json.loads((out / "dolos.json").read_text(encoding="utf-8"))
    assert summary["teams"] == ["equipo-01", "equipo-02", "equipo-03"]
    assert {lang["language"]: lang["compared"] for lang in summary["languages"]} == {
        "java": False,
        "javascript": False,
        "python": True,
    }
    assert summary["excluded"] == {
        "dependency_files": 1,
        "unsupported_files": 2,
        "unsupported_extensions": {"(sin extensión)": 1, ".md": 1},
    }
    assert summary["totals"] == {"team_pairs": 3, "team_pairs_with_matches": 1, "file_pairs": 3}
    # Only the results stay: no info CSV, raw Dolos output or temporary folder.
    assert {p.name for p in out.iterdir()} == {"pairs.csv", "file-pairs.csv", "dolos.json"}


def test_reports_contain_no_code(tmp_path):
    repos = make_repos(tmp_path)
    write_dolos(["equipo-01", "equipo-02"], repos, tmp_path / "reports", runner=FakeDolos())
    for path in (tmp_path / "reports" / "dolos").iterdir():
        assert "sum(i.price" not in path.read_text(encoding="utf-8")


def test_no_shared_language_writes_empty_matrix(tmp_path):
    repos = make_repos(tmp_path)
    fake = FakeDolos()
    result = write_dolos(["equipo-01", "equipo-03"], repos, tmp_path / "reports", runner=fake)
    assert fake.calls == []
    assert [(r["team_a"], r["team_b"], r["similarity"]) for r in result.team_pairs] == [
        ("equipo-01", "equipo-03", 0.0)
    ]


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakeDolos(fail=True), "Dolos \\(python\\) falló: Error: unsupported"),
        (FakeDolos(write_output=False), "no generó pairs.csv"),
    ],
)
def test_failure_keeps_previous_report(tmp_path, fake, message):
    repos = make_repos(tmp_path)
    out = tmp_path / "reports" / "dolos"
    out.mkdir(parents=True)
    (out / "pairs.csv").write_text("previous\n", encoding="utf-8")
    with pytest.raises(DolosError, match=message):
        write_dolos(["equipo-01", "equipo-02"], repos, tmp_path / "reports", runner=fake)
    assert (out / "pairs.csv").read_text(encoding="utf-8") == "previous\n"
    assert [p.name for p in out.iterdir()] == ["pairs.csv"]


def test_dolos_not_installed(tmp_path):
    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    with pytest.raises(DolosError, match="no está instalado"):
        write_dolos(["equipo-01", "equipo-02"], make_repos(tmp_path), tmp_path / "reports", runner=missing)


def test_dolos_timeout(tmp_path):
    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    with pytest.raises(DolosError, match="tardó más de 30 minutos"):
        write_dolos(["equipo-01", "equipo-02"], make_repos(tmp_path), tmp_path / "reports", runner=slow)


def test_write_requires_two_fetched_teams(tmp_path):
    repos = make_repos(tmp_path)
    fake = FakeDolos()
    with pytest.raises(DolosError, match="al menos dos"):
        write_dolos(["equipo-01"], repos, tmp_path / "reports", runner=fake)
    with pytest.raises(DolosError, match="corre 'acr fetch equipo-09'"):
        write_dolos(["equipo-01", "equipo-09"], repos, tmp_path / "reports", runner=fake)
    assert fake.calls == []


@pytest.mark.parametrize("team_id", ["../x", "Equipo", ""])
def test_write_rejects_invalid_id(tmp_path, team_id):
    fake = FakeDolos()
    with pytest.raises(DolosError, match="id inválido"):
        write_dolos(["equipo-01", team_id], make_repos(tmp_path), tmp_path / "reports", runner=fake)
    assert fake.calls == []


# --- CLI -------------------------------------------------------------------------------------


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


def test_cli_dolos(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dolos.subprocess, "run", FakeDolos())
    make_repos(tmp_path)
    config = write_config(tmp_path, ["equipo-01", "equipo-02", "equipo-03"])
    assert main(["--config", str(config), "dolos"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "3 equipo(s), lenguajes comparados: python; 1 de 3 par(es)" in captured.out
    assert "equipo-01 ~ equipo-02: 100%" in captured.out
    assert "2 archivo(s) de lenguajes sin soporte" in captured.err
    assert (tmp_path / "reports" / "dolos" / "pairs.csv").is_file()


def test_cli_dolos_missing_team_compares_the_rest(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dolos.subprocess, "run", FakeDolos())
    make_repos(tmp_path)
    config = write_config(tmp_path, ["equipo-01", "equipo-02", "equipo-09"])
    assert main(["--config", str(config), "dolos"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "equipo-09: aviso: no se compara" in err and "1 equipo(s) sin obtener" in err
    assert len(read_csv(tmp_path / "reports" / "dolos" / "pairs.csv")) == 1


def test_cli_dolos_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dolos.subprocess, "run", FakeDolos(fail=True))
    make_repos(tmp_path)
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "dolos"]) == EXIT_ERROR
    assert "acr dolos: error: Dolos (python) falló" in capsys.readouterr().err
    assert main(["--config", str(tmp_path / "nope.yml"), "dolos"]) == EXIT_ERROR
