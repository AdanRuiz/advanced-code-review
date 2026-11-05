"""Tests for SCANOSS (PB-10, SPEC.md "Detección de plagio").

scanoss-py is never called and there is no network: a fake runner writes a canned WFP for
``fingerprint`` and canned results for ``scan``, and records the WFP that would be sent.
"""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from acr import scanoss
from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.scanoss import ScanossError, build_report, dependency_dir, filter_wfp, write_scanoss

MD5 = "0123456789abcdef0123456789abcdef"

WFP = f"""file={MD5},120,src/main.py
fh2=fedcba9876543210fedcba9876543210
4=1a2b3c4d,5e6f7a8b
9=deadbeef
file={MD5},300,node_modules/left-pad/index.js
3=cafebabe
file={MD5},80,api/vendor/lib/util.go
5=0badf00d
file={MD5},50,README.md
2=12345678
"""

RESULTS = {
    "src/main.py": [
        {
            "id": "snippet",
            "matched": "45%",
            "lines": "4-20",
            "oss_lines": "10-26",
            "component": "flask",
            "vendor": "pallets",
            "version": "3.0.0",
            "purl": ["pkg:github/pallets/flask"],
            "licenses": [{"name": "BSD-3-Clause"}, {"name": "BSD-3-Clause"}, {"name": "MIT"}],
            "url": "https://github.com/pallets/flask",
            "file_url": "https://osskb.org/api/file_contents/abc",
            "file": "src/flask/app.py",
        }
    ],
    "README.md": [{"id": "none"}],
}


class FakeScanoss:
    def __init__(self, wfp: str = WFP, results=None, fail: str | None = None):
        self.wfp = wfp
        self.results = RESULTS if results is None else results
        self.fail = fail
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.sent: list[str] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        self.kwargs.append(kwargs)
        step = cmd[1]
        if step == self.fail:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="x\nAPI limit reached\n")
        output = Path(cmd[cmd.index("--output") + 1])
        if step == "fingerprint":
            output.write_text(self.wfp, encoding="utf-8")
        elif step == "scan":
            self.sent.append(Path(cmd[cmd.index("--wfp") + 1]).read_text(encoding="utf-8"))
            text = self.results if isinstance(self.results, str) else json.dumps(self.results)
            output.write_text(text, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def make_repo(root: Path, team_id: str = "equipo-01") -> Path:
    repo = root / "repos" / team_id
    repo.mkdir(parents=True)
    (repo / "main.py").write_text("SECRET_CODE = 1\n", encoding="utf-8")
    return repo


# --- dependency exclusion and fingerprint checks ---------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("node_modules/a/index.js", "node_modules"),
        ("web/Node_Modules/a.js", "web/Node_Modules"),
        ("api/vendor/lib/util.go", "api/vendor"),
        ("back\\.venv\\lib\\x.py", "back/.venv"),
        ("ios/Pods/x.m", "ios/Pods"),
        ("src/main.py", None),
        ("src/vendor.py", None),
        ("vendor", None),
    ],
)
def test_dependency_dir(path, expected):
    assert dependency_dir(path) == expected


def test_filter_wfp_drops_dependencies(tmp_path):
    prints = filter_wfp(WFP, tmp_path)
    assert prints.files == ["src/main.py", "README.md"]
    assert prints.excluded_files == 2
    assert prints.excluded_dirs == {"node_modules", "api/vendor"}
    assert "node_modules" not in prints.wfp and "vendor" not in prints.wfp
    assert "cafebabe" not in prints.wfp and "0badf00d" not in prints.wfp
    assert prints.wfp.splitlines() == [
        f"file={MD5},120,src/main.py",
        "fh2=fedcba9876543210fedcba9876543210",
        "4=1a2b3c4d,5e6f7a8b",
        "9=deadbeef",
        f"file={MD5},50,README.md",
        "2=12345678",
    ]


def test_filter_wfp_makes_absolute_paths_relative(tmp_path):
    repo = tmp_path / "repos" / "equipo-01"
    wfp = f"file={MD5},10,{repo.resolve().as_posix()}/node_modules/x.js\n1=aaaaaaaa\n"
    wfp += f"file={MD5},10,{repo.resolve().as_posix()}/app.js\n1=bbbbbbbb\n"
    prints = filter_wfp(wfp, repo)
    assert prints.files == ["app.js"] and prints.excluded_files == 1


@pytest.mark.parametrize(
    "line",
    ["4=print('hola')", "SECRET_CODE = 1", "4=1A2B3C4D", "bin=abc", "fh2=xyz", "4=1a2b3c"],
)
def test_filter_wfp_refuses_anything_but_hashes(tmp_path, line):
    with pytest.raises(ScanossError, match="no es un hash"):
        filter_wfp(f"file={MD5},10,a.py\n{line}\n", tmp_path)


def test_filter_wfp_refuses_hash_without_file(tmp_path):
    with pytest.raises(ScanossError, match="file="):
        filter_wfp("4=1a2b3c4d\n", tmp_path)


# --- building the report --------------------------------------------------------------------


def test_report_has_project_license_and_link(tmp_path):
    report = build_report("equipo-01", filter_wfp(WFP, tmp_path), RESULTS)
    assert report["schema_version"] == 1 and report["tool"] == "scanoss"
    assert report["totals"] == {
        "files_scanned": 2,
        "files_matched": 1,
        "matched_percent": 50.0,
        "file_matches": 0,
        "snippet_matches": 1,
    }
    assert report["excluded"] == {"files": 2, "dirs": ["api/vendor", "node_modules"]}
    (match,) = report["matches"]
    assert match == {
        "path": "src/main.py",
        "match": "snippet",
        "matched_percent": 45.0,
        "lines": "4-20",
        "oss_lines": "10-26",
        "component": "flask",
        "vendor": "pallets",
        "version": "3.0.0",
        "purl": "pkg:github/pallets/flask",
        "licenses": ["BSD-3-Clause", "MIT"],
        "url": "https://github.com/pallets/flask",
        "file_url": "https://osskb.org/api/file_contents/abc",
        "source_file": "src/flask/app.py",
    }


def test_report_tolerates_missing_fields(tmp_path):
    results = {"README.md": [{"id": "file", "matched": "n/a", "purl": [], "licenses": None}]}
    (match,) = build_report("equipo-01", filter_wfp(WFP, tmp_path), results)["matches"]
    assert match["match"] == "file" and match["matched_percent"] is None
    assert match["purl"] is None and match["licenses"] == [] and match["component"] is None


# --- running scanoss-py ---------------------------------------------------------------------


def test_write_scanoss_sends_only_filtered_fingerprints(tmp_path):
    repo = make_repo(tmp_path)
    fake = FakeScanoss()
    path, report = write_scanoss("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=fake)

    fingerprint_cmd, scan_cmd = fake.calls
    assert fingerprint_cmd[:2] == ["scanoss-py", "fingerprint"]
    assert fingerprint_cmd[-1] == str(repo.resolve())
    assert scan_cmd[:2] == ["scanoss-py", "scan"] and "--wfp" in scan_cmd
    # The process that talks to the API never gets the repository.
    assert not any(str(repo.resolve()) in arg for arg in scan_cmd)
    for kwargs in fake.kwargs:
        assert kwargs.get("shell") is not True
        assert kwargs["timeout"] > 0
        assert not str(kwargs["cwd"]).startswith(str(repo.resolve()))
    (sent,) = fake.sent
    assert "SECRET_CODE" not in sent and "node_modules" not in sent and "vendor" not in sent
    assert sent == filter_wfp(WFP, repo).wfp

    assert path == tmp_path / "reports" / "equipo-01" / "scanoss.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert report["totals"]["files_matched"] == 1
    assert not list(path.parent.glob(".*.tmp"))


def test_only_dependencies_sends_nothing(tmp_path):
    make_repo(tmp_path)
    fake = FakeScanoss(wfp=f"file={MD5},10,node_modules/a.js\n1=aaaaaaaa\n")
    _, report = write_scanoss("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=fake)
    assert [cmd[1] for cmd in fake.calls] == ["fingerprint"]
    assert report["totals"]["files_scanned"] == 0 and report["matches"] == []


def test_suspicious_fingerprint_sends_nothing(tmp_path):
    make_repo(tmp_path)
    fake = FakeScanoss(wfp=f"file={MD5},10,a.py\nSECRET_CODE = 1\n")
    with pytest.raises(ScanossError, match="no se envía nada"):
        write_scanoss("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=fake)
    assert [cmd[1] for cmd in fake.calls] == ["fingerprint"]


def test_scanoss_not_installed(tmp_path):
    make_repo(tmp_path)

    def missing(cmd, **kwargs):
        raise FileNotFoundError("scanoss-py")

    with pytest.raises(ScanossError, match="no está instalado"):
        write_scanoss("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=missing)


def test_scanoss_timeout(tmp_path):
    make_repo(tmp_path)

    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    with pytest.raises(ScanossError, match="tardó"):
        write_scanoss("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=slow)


@pytest.mark.parametrize("step", ["fingerprint", "scan"])
def test_failure_keeps_previous_report(tmp_path, step):
    make_repo(tmp_path)
    path, report = write_scanoss("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=FakeScanoss())
    with pytest.raises(ScanossError, match="API limit reached"):
        write_scanoss("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=FakeScanoss(fail=step))
    assert json.loads(path.read_text(encoding="utf-8")) == report


@pytest.mark.parametrize("results", ["not json", "[1, 2]", '{"a.py": {"id": "file"}}'])
def test_unexpected_scan_output(tmp_path, results):
    make_repo(tmp_path)
    with pytest.raises(ScanossError):
        write_scanoss(
            "equipo-01", tmp_path / "repos", tmp_path / "reports", runner=FakeScanoss(results=results)
        )


def test_write_requires_fetched_repo(tmp_path):
    fake = FakeScanoss()
    with pytest.raises(ScanossError, match="acr fetch"):
        write_scanoss("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=fake)
    assert fake.calls == []


@pytest.mark.parametrize("team_id", ["../x", "A", ""])
def test_write_rejects_invalid_id(tmp_path, team_id):
    fake = FakeScanoss()
    with pytest.raises(ScanossError, match="id inválido"):
        write_scanoss(team_id, tmp_path / "repos", tmp_path / "reports", runner=fake)
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


def test_cli_scanoss_all_and_continue_on_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scanoss.subprocess, "run", FakeScanoss())
    make_repo(tmp_path, "equipo-02")
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "scanoss", "equipo-02"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "equipo-02: 1 de 2 archivo(s) con coincidencias (50.0%), 2 archivo(s) de dependencias" in out
    assert main(["--config", str(config), "scanoss"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "equipo-01: error" in err and "1 de 2" in err
    assert (tmp_path / "reports" / "equipo-02" / "scanoss.json").is_file()


def test_cli_scanoss_unknown_team_and_bad_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = write_config(tmp_path, ["equipo-01"])
    assert main(["--config", str(config), "scanoss", "otro"]) == EXIT_ERROR
    assert main(["--config", str(tmp_path / "nope.yml"), "scanoss"]) == EXIT_ERROR
