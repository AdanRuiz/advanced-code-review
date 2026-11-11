"""Tests for the MegaLinter run (PB-9, SPEC.md "MegaLinter").

No container is started: a fake runner records the command and writes the files MegaLinter
would leave in the reports folder mounted in the container.
"""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from acr import megalinter
from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.megalinter import (
    CONFIG_TARGET,
    DEFAULT_CONFIG_FILE,
    REPORTS_TARGET,
    WORKSPACE,
    MegaLinterError,
    build_command,
    find_engine,
    run_megalinter,
)

ROOT = Path(__file__).resolve().parent.parent


def mounts(cmd: list[str]) -> dict[str, dict[str, str]]:
    """target -> mount options, from the --mount arguments of a container command."""
    result = {}
    for flag, value in zip(cmd, cmd[1:], strict=False):
        if flag == "--mount":
            options = dict(part.split("=", 1) for part in value.split(","))
            result[options["target"]] = options
    return result


def envs(cmd: list[str]) -> dict[str, str]:
    return dict(value.split("=", 1) for flag, value in zip(cmd, cmd[1:], strict=False) if flag == "-e")


class FakeMegaLinter:
    def __init__(
        self,
        code: int = 1,
        stderr: str = "",
        reports: tuple[str, ...] = ("megalinter.sarif", "megalinter.json", "megalinter.md"),
        sarif: tuple[str, ...] = ("REPOSITORY_TRIVY.sarif", "REPOSITORY_SEMGREP.sarif", "PYTHON_RUFF.sarif"),
        json_text: str = '{"status": "error", "linters": []}',
    ):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.code, self.stderr = code, stderr
        self.reports, self.sarif, self.json_text = reports, sarif, json_text

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        self.kwargs.append(kwargs)
        out = Path(mounts(cmd)[REPORTS_TARGET]["source"])
        for name in self.reports:
            text = self.json_text if name.endswith(".json") else f"{name} run {len(self.calls)}"
            (out / name).write_text(text, encoding="utf-8")
        (out / "sarif").mkdir()
        for name in self.sarif:
            (out / "sarif" / name).write_text(f"{name} run {len(self.calls)}", encoding="utf-8")
        (out / "linters_logs").mkdir()
        (out / "linters_logs" / "ERROR-PYTHON_RUFF.log").write_text("x", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, self.code, stdout="", stderr=self.stderr)


def always(name):
    return f"/usr/bin/{name}"


def make_repo(root: Path, team_id: str = "equipo-01") -> Path:
    repo = root / "repos" / team_id
    repo.mkdir(parents=True)
    (repo / "main.py").write_text("print('hola')\n", encoding="utf-8")
    return repo


def run(tmp_path, fake, **kwargs):
    kwargs.setdefault("which", always)
    temp = tmp_path / "tmp"
    temp.mkdir(exist_ok=True)
    kwargs.setdefault("temp_dir", temp)
    return run_megalinter("equipo-01", tmp_path / "repos", tmp_path / "reports", runner=fake, **kwargs)


# --- .mega-linter.yml ----------------------------------------------------------------------


def test_config_file_has_spec_linters_and_reporters():
    assert DEFAULT_CONFIG_FILE == ROOT / ".mega-linter.yml"
    config = yaml.safe_load(DEFAULT_CONFIG_FILE.read_text(encoding="utf-8"))
    disabled = set(config["DISABLE_LINTERS"])
    # Secrets: Betterleaks active; Gitleaks, TruffleHog and Secretlint disabled.
    assert {"REPOSITORY_GITLEAKS", "REPOSITORY_TRUFFLEHOG", "REPOSITORY_SECRETLINT"} <= disabled
    # Dependencies: Trivy active; OSV-Scanner and Grype disabled.
    assert {"REPOSITORY_OSV_SCANNER", "REPOSITORY_GRYPE"} <= disabled
    active = {"REPOSITORY_BETTERLEAKS", "REPOSITORY_SEMGREP", "REPOSITORY_TRIVY"}
    assert not active & disabled
    # Language linters are detected automatically: no allow-list.
    assert "ENABLE_LINTERS" not in config and "ENABLE" not in config
    assert config["VALIDATE_ALL_CODEBASE"] is True
    assert config["APPLY_FIXES"] == "none"
    # SARIF, JSON and Markdown with the names acr expects.
    assert config["SARIF_REPORTER"] and config["JSON_REPORTER"] and config["MARKDOWN_SUMMARY_REPORTER"]
    assert config["SARIF_REPORTER_FILE_NAME"] == "megalinter.sarif"
    assert config["JSON_REPORTER_FILE_NAME"] == "megalinter.json"
    assert config["MARKDOWN_SUMMARY_REPORTER_FILE_NAME"] == "megalinter.md"


def test_config_file_sends_nothing_out_and_hides_secrets():
    config = yaml.safe_load(DEFAULT_CONFIG_FILE.read_text(encoding="utf-8"))
    for key in ("API_REPORTER", "LLM_ADVISOR_ENABLED", "FILEIO_REPORTER", "EMAIL_REPORTER",
                "GITHUB_COMMENT_REPORTER", "GITLAB_COMMENT_REPORTER", "AZURE_COMMENT_REPORTER",
                "BITBUCKET_COMMENT_REPORTER", "UPDATED_SOURCES_REPORTER"):
        assert config[key] is False, key
    # Linters that reach the network with repository data, or duplicate Betterleaks/Semgrep.
    disabled = set(config["DISABLE_LINTERS"])
    assert {"SPELL_LYCHEE", "REPOSITORY_DUSTILOCK", "REPOSITORY_KINGFISHER", "REPOSITORY_DEVSKIM"} <= disabled
    assert "--metrics=off" in config["REPOSITORY_SEMGREP_ARGUMENTS"]
    assert "--redact" in config["REPOSITORY_BETTERLEAKS_ARGUMENTS"]


# --- container engine and command ----------------------------------------------------------


def test_find_engine_prefers_podman_then_docker():
    assert find_engine(which=always) == "podman"
    assert find_engine(which=lambda name: always(name) if name == "docker" else None) == "docker"
    assert find_engine("docker", which=always) == "docker"


def test_find_engine_missing_or_unsupported():
    with pytest.raises(MegaLinterError, match="PB-1"):
        find_engine(which=lambda name: None)
    with pytest.raises(MegaLinterError, match="docker"):
        find_engine("docker", which=lambda name: always(name) if name == "podman" else None)
    with pytest.raises(MegaLinterError, match="no soportado"):
        find_engine("nerdctl", which=always)


def test_command_mounts_workspace_copy_config_read_only_and_reports_outside(tmp_path):
    workspace = make_repo(tmp_path)
    out = tmp_path / "reports" / "equipo-01" / ".megalinter.tmp"
    cmd = build_command("podman", "img:v10", "equipo-01", workspace, out, DEFAULT_CONFIG_FILE)
    assert cmd[:5] == ["podman", "run", "--rm", "--name", "acr-megalinter-equipo-01"]
    assert cmd[-1] == "img:v10"
    m = mounts(cmd)
    # MegaLinter v10 writes temporary files in the workspace: it gets a writable disposable copy.
    assert m[WORKSPACE]["source"] == str(workspace.resolve()) and "readonly" not in m[WORKSPACE]
    assert m[CONFIG_TARGET]["source"] == str(DEFAULT_CONFIG_FILE) and m[CONFIG_TARGET]["readonly"] == "true"
    assert m[REPORTS_TARGET]["source"] == str(out.resolve()) and "readonly" not in m[REPORTS_TARGET]
    assert all(options["type"] == "bind" for options in m.values())
    assert envs(cmd) == {
        "DEFAULT_WORKSPACE": WORKSPACE,
        "MEGALINTER_CONFIG": CONFIG_TARGET,
        "REPORT_OUTPUT_FOLDER": REPORTS_TARGET,
    }
    # Reports are never written inside the mounted repository.
    assert not REPORTS_TARGET.startswith(WORKSPACE)


@pytest.mark.parametrize("engine", ["podman", "docker"])
def test_command_is_hardened(tmp_path, engine):
    repo = make_repo(tmp_path)
    out = tmp_path / "reports" / "equipo-01" / ".megalinter.tmp"
    cmd = build_command(engine, "img:v10", "equipo-01", repo, out, DEFAULT_CONFIG_FILE)
    options = cmd[: cmd.index("img:v10")]  # everything after the image goes to the container
    assert "--cap-drop=ALL" in options
    assert "--security-opt=no-new-privileges" in options
    assert f"--pids-limit={megalinter.PIDS_LIMIT}" in options
    assert f"--memory={megalinter.MEMORY_LIMIT}" in options
    assert f"--memory-swap={megalinter.MEMORY_LIMIT}" in options
    assert 0 < megalinter.PIDS_LIMIT and megalinter.MEMORY_LIMIT[:-1].isdigit()
    # Nothing from the host is mounted except the repository, the configuration and the reports.
    assert set(mounts(cmd)) == {WORKSPACE, CONFIG_TARGET, REPORTS_TARGET}
    assert options.count("--mount") == 3
    for option in options:
        name = option.split("=", 1)[0]
        assert name not in {"-v", "--volume", "--volumes-from", "--privileged", "--cap-add",
                            "--device", "--pid", "--ipc", "--userns", "--env-file"}, option
        assert not option.startswith(("--network=host", "--net=host", "--security-opt=seccomp=unconfined"))
    # The options come before the image, so the engine applies them.
    assert cmd.index("--cap-drop=ALL") < cmd.index("img:v10")


def test_run_uses_hardened_command(tmp_path):
    make_repo(tmp_path)
    fake = FakeMegaLinter()
    run(tmp_path, fake)
    (cmd,) = fake.calls
    for option in megalinter.HARDENING_OPTIONS:
        assert option in cmd


def test_command_rejects_paths_that_break_mount_syntax(tmp_path):
    repo = tmp_path / "a,b"
    repo.mkdir()
    with pytest.raises(MegaLinterError, match="no puede contener"):
        build_command("podman", "img", "equipo-01", repo, tmp_path, DEFAULT_CONFIG_FILE)


def test_default_image_is_megalinter_v10():
    # v10 is published on ghcr.io; docker.io/oxsecurity/megalinter stops at v9.
    assert megalinter.DEFAULT_IMAGE == "ghcr.io/oxsecurity/megalinter:v10"


# --- disposable workspace copy -------------------------------------------------------------


def test_run_mounts_a_copy_not_the_fetched_repo_and_deletes_it(tmp_path):
    repo = make_repo(tmp_path)
    seen = {}

    class Recorder(FakeMegaLinter):
        def __call__(self, cmd, **kwargs):
            source = Path(mounts(cmd)[WORKSPACE]["source"])
            seen["source"] = source
            seen["content"] = (source / "main.py").read_text(encoding="utf-8")
            (source / "megalinter_temp.txt").write_text("x", encoding="utf-8")  # writable
            return super().__call__(cmd, **kwargs)

    run(tmp_path, Recorder())
    source = seen["source"]
    assert source != repo.resolve() and repo.resolve() not in source.parents
    assert (tmp_path / "tmp").resolve() in source.parents
    assert seen["content"] == "print('hola')\n"
    assert not source.exists() and not source.parent.exists()  # copy deleted
    assert sorted(p.name for p in repo.iterdir()) == ["main.py"]  # original untouched


def test_copy_is_deleted_when_megalinter_fails(tmp_path):
    make_repo(tmp_path)
    with pytest.raises(MegaLinterError):
        run(tmp_path, FakeMegaLinter(code=2, stderr="boom"))
    assert list((tmp_path / "tmp").iterdir()) == []


def test_writable_copy_of_read_only_repo(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "sub").mkdir()
    (repo / "sub" / "a.txt").write_text("a", encoding="utf-8")
    from acr.fetch import make_read_only, remove_tree

    make_read_only(repo)
    try:
        copy = megalinter.writable_copy(repo, tmp_path, "equipo-01")
        (copy / "new.txt").write_text("x", encoding="utf-8")
        (copy / "sub" / "a.txt").write_text("changed", encoding="utf-8")
        assert (repo / "sub" / "a.txt").read_text(encoding="utf-8") == "a"
        remove_tree(copy.parent)
    finally:
        remove_tree(repo)


def test_writable_copy_keeps_symlinks_as_links(tmp_path):
    repo = make_repo(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("host secret", encoding="utf-8")
    try:
        (repo / "link").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not available on this system")
    copy = megalinter.writable_copy(repo, tmp_path, "equipo-01")
    assert (copy / "link").is_symlink()  # the host file is not copied into the workspace


# --- running -------------------------------------------------------------------------------


def test_run_writes_sarif_json_and_markdown(tmp_path):
    make_repo(tmp_path)
    fake = FakeMegaLinter(code=1)
    result = run(tmp_path, fake, engine="docker")
    out = tmp_path / "reports" / "equipo-01"
    (cmd,), (kwargs,) = fake.calls, fake.kwargs
    assert cmd[0] == "docker"
    assert kwargs.get("shell") is not True
    assert kwargs["timeout"] == megalinter.DEFAULT_TIMEOUT_SECONDS
    assert result.returncode == 1
    assert result.missing == []
    assert sorted(p.name for p in result.files) == [
        "megalinter.json", "megalinter.md", "megalinter.sarif", "semgrep.sarif", "trivy.sarif",
    ]
    assert (out / "trivy.sarif").read_text(encoding="utf-8") == "REPOSITORY_TRIVY.sarif run 1"
    assert (out / "semgrep.sarif").read_text(encoding="utf-8") == "REPOSITORY_SEMGREP.sarif run 1"
    assert json.loads((out / "megalinter.json").read_text(encoding="utf-8"))["status"] == "error"
    assert (out / "megalinter" / "linters_logs" / "ERROR-PYTHON_RUFF.log").is_file()
    assert not (out / ".megalinter.tmp").exists()
    assert not list(out.glob(".*.tmp"))
    # The repository is untouched.
    assert sorted(p.name for p in (tmp_path / "repos" / "equipo-01").iterdir()) == ["main.py"]


def test_rerun_replaces_reports_and_drops_stale_linter_sarif(tmp_path):
    make_repo(tmp_path)
    run(tmp_path, FakeMegaLinter())
    result = run(tmp_path, FakeMegaLinter(code=0, sarif=("REPOSITORY_SEMGREP.sarif",)))
    out = tmp_path / "reports" / "equipo-01"
    assert result.returncode == 0
    assert result.missing == ["trivy.sarif"]
    assert not (out / "trivy.sarif").exists()
    assert not (out / "megalinter" / "sarif" / "REPOSITORY_TRIVY.sarif").exists()


@pytest.mark.parametrize("code", [2, 125, -9])
def test_engine_failure_keeps_previous_reports(tmp_path, code):
    make_repo(tmp_path)
    run(tmp_path, FakeMegaLinter())
    out = tmp_path / "reports" / "equipo-01"
    before = (out / "megalinter.sarif").read_text(encoding="utf-8")
    with pytest.raises(MegaLinterError, match="no such image"):
        run(tmp_path, FakeMegaLinter(code=code, stderr="pulling\nError: no such image\n"))
    assert (out / "megalinter.sarif").read_text(encoding="utf-8") == before
    assert (out / "trivy.sarif").is_file()
    assert not (out / ".megalinter.tmp").exists()


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakeMegaLinter(reports=("megalinter.sarif", "megalinter.md")), "megalinter.json"),
        (FakeMegaLinter(json_text="not json"), "JSON válido"),
        (FakeMegaLinter(json_text="[1]"), "formato"),
    ],
)
def test_missing_or_invalid_reports_are_an_error(tmp_path, fake, message):
    make_repo(tmp_path)
    with pytest.raises(MegaLinterError, match=message):
        run(tmp_path, fake)
    out = tmp_path / "reports" / "equipo-01"
    assert not (out / "megalinter").exists()
    assert not (out / ".megalinter.tmp").exists()


def test_engine_not_installed(tmp_path):
    make_repo(tmp_path)

    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    with pytest.raises(MegaLinterError, match="no está instalado"):
        run(tmp_path, missing)


def test_timeout_removes_the_container(tmp_path):
    make_repo(tmp_path)
    calls = []

    def slow(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "run":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return subprocess.CompletedProcess(cmd, 0)

    with pytest.raises(MegaLinterError, match="tardó más de 5 minutos"):
        run(tmp_path, slow, timeout=300)
    assert calls[-1] == ["podman", "rm", "--force", "acr-megalinter-equipo-01"]


def test_requires_fetched_repo_and_valid_id(tmp_path):
    fake = FakeMegaLinter()
    with pytest.raises(MegaLinterError, match="acr fetch"):
        run(tmp_path, fake)
    for team_id in ("../x", "A", ""):
        with pytest.raises(MegaLinterError, match="id inválido"):
            run_megalinter(team_id, tmp_path / "repos", tmp_path / "reports", runner=fake, which=always)
    assert fake.calls == []


def test_requires_config_file(tmp_path):
    make_repo(tmp_path)
    fake = FakeMegaLinter()
    with pytest.raises(MegaLinterError, match="configuración"):
        run(tmp_path, fake, config_file=tmp_path / "nope.yml")
    assert fake.calls == []


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


@pytest.fixture
def fake_engine(monkeypatch):
    fake = FakeMegaLinter()
    monkeypatch.setattr(megalinter.subprocess, "run", fake)
    monkeypatch.setattr(megalinter.shutil, "which", always)
    return fake


def test_cli_megalinter_all(tmp_path, monkeypatch, capsys, fake_engine):
    monkeypatch.chdir(tmp_path)
    make_repo(tmp_path, "equipo-01")
    make_repo(tmp_path, "equipo-02")
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    args = ["--config", str(config), "megalinter", "--engine", "docker", "--timeout-min", "2"]
    assert main(args) == EXIT_OK
    out = capsys.readouterr().out
    assert "equipo-01: MegaLinter con errores de linters" in out
    assert [cmd[0] for cmd in fake_engine.calls] == ["docker", "docker"]
    assert all(kwargs["timeout"] == 120 for kwargs in fake_engine.kwargs)
    for team_id in ("equipo-01", "equipo-02"):
        for name in ("megalinter.sarif", "megalinter.json", "megalinter.md"):
            assert (tmp_path / "reports" / team_id / name).is_file()


def test_cli_megalinter_one_team_and_continue_on_error(tmp_path, monkeypatch, capsys, fake_engine):
    monkeypatch.chdir(tmp_path)
    make_repo(tmp_path, "equipo-02")
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "megalinter", "equipo-02"]) == EXIT_OK
    assert main(["--config", str(config), "megalinter"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "equipo-01: error" in err and "1 de 2" in err


def test_cli_megalinter_unknown_team_and_bad_config(tmp_path, monkeypatch, fake_engine):
    monkeypatch.chdir(tmp_path)
    config = write_config(tmp_path, ["equipo-01"])
    assert main(["--config", str(config), "megalinter", "otro"]) == EXIT_ERROR
    assert main(["--config", str(tmp_path / "nope.yml"), "megalinter"]) == EXIT_ERROR
    assert fake_engine.calls == []


# --- rootful Docker (acr ci, PB-62) --------------------------------------------------------


def test_missing_reports_show_megalinter_last_line_without_secrets(tmp_path):
    make_repo(tmp_path)
    secret = "ghp_" + "Q" * 36

    def quiet(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=f"line 1\nPermission denied {secret}\n", stderr="")

    with pytest.raises(MegaLinterError, match="Permission denied") as info:
        run(tmp_path, quiet)
    assert secret not in str(info.value) and "[REDACTED]" in str(info.value)


def test_open_permissions_lets_the_container_write(tmp_path):
    import os
    import stat

    make_repo(tmp_path)
    seen = {}

    def check(cmd, **kwargs):
        found = mounts(cmd)
        for target in (REPORTS_TARGET, megalinter.WORKSPACE):
            seen[target] = stat.S_IMODE(os.stat(found[target]["source"]).st_mode)
        return FakeMegaLinter()(cmd, **kwargs)

    run(tmp_path, check, open_permissions=True)
    if os.name == "nt":
        pytest.skip("Windows does not keep POSIX modes")
    assert seen == {REPORTS_TARGET: 0o777, megalinter.WORKSPACE: 0o777}
