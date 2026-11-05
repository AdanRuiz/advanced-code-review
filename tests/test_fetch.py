"""Tests for fetching repositories (PB-6, SPEC.md "Entrada" and "Entorno").

git is never called: a fake runner records the commands and creates the clone folder.
"""

import os
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.config import Team
from acr.fetch import (
    FetchError,
    classify_source,
    extract_zip,
    fetch_team,
    is_git_url,
    remove_tree,
)


def make_team(repo: str, team_id: str = "equipo-01", rama=None, commit=None) -> Team:
    return Team(
        id=team_id, nombre="N", proyecto="P", objetivo="O", repo=repo, rama=rama, commit=commit
    )


class FakeGit:
    def __init__(self, returncode: int = 0, stderr: str = "", fail_on: str | None = None):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.returncode = returncode
        self.stderr = stderr
        self.fail_on = fail_on

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        self.kwargs.append(kwargs)
        if "clone" in cmd:
            dest = Path(cmd[-1])
            dest.mkdir(parents=True)
            (dest / "README.md").write_text("hola", encoding="utf-8")
        failing = self.fail_on is None or self.fail_on in cmd
        code = self.returncode if failing else 0
        return subprocess.CompletedProcess(cmd, code, stdout="", stderr=self.stderr if code else "")


def write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


# --- classification ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "repo",
    [
        "https://github.com/org/proyecto",
        "http://gitlab.local/org/p.git",
        "ssh://git@github.com/org/p.git",
        "git@github.com:org/p.git",
        "git://example.org/p.git",
    ],
)
def test_git_urls(repo):
    assert is_git_url(repo)
    assert classify_source(repo) == "git"


@pytest.mark.parametrize("repo", ["ext::sh -c touch% /tmp/x", "file:///etc", "ftp://x/y", "--upload-pack=x"])
def test_disallowed_sources(repo):
    with pytest.raises(FetchError):
        classify_source(repo)


def test_windows_drive_path_is_not_a_git_url():
    assert not is_git_url("C:/entregas/proyecto")


def test_local_folder_and_zip(tmp_path):
    folder = tmp_path / "proyecto"
    folder.mkdir()
    archive = write_zip(tmp_path / "p.ZIP", {"a.py": b"x"})
    assert classify_source(str(folder)) == "folder"
    assert classify_source(str(archive)) == "zip"


def test_missing_local_sources(tmp_path):
    with pytest.raises(FetchError, match="carpeta"):
        classify_source(str(tmp_path / "nope"))
    with pytest.raises(FetchError, match="ZIP"):
        classify_source(str(tmp_path / "nope.zip"))


# --- git -----------------------------------------------------------------------------------


def test_git_clone_default_branch(tmp_path):
    git = FakeGit()
    result = fetch_team(make_team("https://github.com/org/p"), tmp_path / "repos", runner=git)
    assert result.kind == "git"
    assert result.path == tmp_path / "repos" / "equipo-01"
    assert (result.path / "README.md").read_text(encoding="utf-8") == "hola"
    assert len(git.calls) == 1
    cmd = git.calls[0]
    assert cmd[0] == "git" and "clone" in cmd
    assert not any(arg.startswith("--branch") for arg in cmd)
    assert "--depth" not in " ".join(cmd)  # full history for the git signals (PB-7)
    assert cmd[-3:] == ["--", "https://github.com/org/p", str(tmp_path / "repos" / ".equipo-01.tmp")]
    assert "core.symlinks=false" in cmd
    assert git.kwargs[0]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "shell" not in git.kwargs[0]


def test_git_respects_branch_and_commit(tmp_path):
    git = FakeGit()
    team = make_team("https://github.com/org/p", rama="develop", commit="a1b2c3d")
    fetch_team(team, tmp_path / "repos", runner=git)
    clone, checkout = git.calls
    assert "--branch=develop" in clone
    assert checkout[:3] == ["git", "-C", str(tmp_path / "repos" / ".equipo-01.tmp")]
    assert checkout[-4:] == ["checkout", "--detach", "a1b2c3d", "--"]


@pytest.mark.parametrize(
    ("rama", "commit"),
    [("-x", None), ("a..b", None), ("main lock", None), (None, "--help"), (None, "zzzzzzz"), (None, "a1")],
)
def test_git_rejects_bad_refs(tmp_path, rama, commit):
    git = FakeGit()
    with pytest.raises(FetchError):
        fetch_team(make_team("https://github.com/org/p", rama=rama, commit=commit), tmp_path, runner=git)
    assert git.calls == []


def test_git_clone_failure_leaves_nothing(tmp_path):
    repos = tmp_path / "repos"
    git = FakeGit(returncode=128, stderr="fatal: repository not found")
    with pytest.raises(FetchError, match="repository not found"):
        fetch_team(make_team("https://github.com/org/p"), repos, runner=git)
    assert list(repos.iterdir()) == []


def test_git_unknown_commit_keeps_previous_copy(tmp_path):
    repos = tmp_path / "repos"
    fetch_team(make_team("https://github.com/org/p"), repos, runner=FakeGit())
    git = FakeGit(returncode=1, stderr="error: pathspec 'abcdef1' did not match", fail_on="checkout")
    with pytest.raises(FetchError, match="checkout"):
        fetch_team(make_team("https://github.com/org/p", commit="abcdef1"), repos, runner=git)
    assert sorted(p.name for p in repos.iterdir()) == ["equipo-01"]
    assert (repos / "equipo-01" / "README.md").exists()


def test_git_not_installed(tmp_path):
    def missing(cmd, **kwargs):
        raise FileNotFoundError("git")

    with pytest.raises(FetchError, match="no está instalado"):
        fetch_team(make_team("https://github.com/org/p"), tmp_path, runner=missing)


# --- folder --------------------------------------------------------------------------------


def test_folder_copy_and_read_only(tmp_path):
    source = tmp_path / "entrega"
    (source / "src").mkdir(parents=True)
    (source / "src" / "app.py").write_text("print(1)", encoding="utf-8")
    result = fetch_team(make_team(str(source)), tmp_path / "repos")
    copied = result.path / "src" / "app.py"
    assert result.kind == "folder"
    assert copied.read_text(encoding="utf-8") == "print(1)"
    assert not os.stat(copied).st_mode & stat.S_IWUSR
    assert (source / "src" / "app.py").exists()  # the source is untouched


def test_folder_refetch_replaces_read_only_copy(tmp_path):
    source = tmp_path / "entrega"
    source.mkdir()
    (source / "old.py").write_text("1", encoding="utf-8")
    repos = tmp_path / "repos"
    fetch_team(make_team(str(source)), repos)
    (source / "old.py").unlink()
    (source / "new.py").write_text("2", encoding="utf-8")
    fetch_team(make_team(str(source)), repos)
    assert sorted(p.name for p in (repos / "equipo-01").iterdir()) == ["new.py"]


def test_folder_containing_destination_is_rejected(tmp_path):
    with pytest.raises(FetchError, match="contiene"):
        fetch_team(make_team(str(tmp_path)), tmp_path / "repos")


def test_folder_rejects_branch_and_commit(tmp_path):
    with pytest.raises(FetchError, match="URL git"):
        fetch_team(make_team(str(tmp_path), rama="main"), tmp_path / "repos")


def test_folder_skips_symlinks(tmp_path):
    outside = tmp_path / "secreto.txt"
    outside.write_text("no", encoding="utf-8")
    source = tmp_path / "entrega"
    source.mkdir()
    (source / "a.py").write_text("x", encoding="utf-8")
    try:
        (source / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("este sistema no permite crear enlaces simbólicos")
    result = fetch_team(make_team(str(source)), tmp_path / "repos")
    assert sorted(p.name for p in result.path.iterdir()) == ["a.py"]


# --- ZIP -----------------------------------------------------------------------------------


def test_zip_extract(tmp_path):
    archive = write_zip(tmp_path / "p.zip", {"src/app.py": b"print(1)", "docs/": b""})
    result = fetch_team(make_team(str(archive)), tmp_path / "repos")
    assert result.kind == "zip"
    assert (result.path / "src" / "app.py").read_bytes() == b"print(1)"
    assert (result.path / "docs").is_dir()


@pytest.mark.parametrize(
    "name", ["../evil.py", "a/../../evil.py", "/abs/evil.py", "C:/evil.py", "..\\evil.py", "c:evil.py"]
)
def test_zip_slip_is_rejected(tmp_path, name):
    archive = write_zip(tmp_path / "p.zip", {"ok.py": b"1", name: b"evil"})
    repos = tmp_path / "repos"
    with pytest.raises(FetchError, match="peligrosa"):
        fetch_team(make_team(str(archive)), repos)
    assert not (tmp_path / "evil.py").exists()
    assert list(repos.iterdir()) == []


def test_zip_symlink_entry_is_rejected(tmp_path):
    archive = tmp_path / "p.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(FetchError, match="enlace"):
        extract_zip(archive, tmp_path / "out")


def test_zip_size_limit(tmp_path):
    archive = write_zip(tmp_path / "p.zip", {"big.bin": b"0" * 5000})
    repos = tmp_path / "repos"
    with pytest.raises(FetchError, match="límite"):
        fetch_team(make_team(str(archive)), repos, max_zip_bytes=4096)
    assert list(repos.iterdir()) == []


def test_zip_size_limit_counts_every_file(tmp_path):
    archive = write_zip(tmp_path / "p.zip", {"a.bin": b"0" * 3000, "b.bin": b"1" * 3000})
    extract_zip(archive, tmp_path / "ok", max_bytes=6000)
    with pytest.raises(FetchError, match="límite"):
        extract_zip(archive, tmp_path / "out", max_bytes=5999)
    assert not (tmp_path / "out").exists()


def test_zip_file_count_limit(tmp_path):
    archive = write_zip(tmp_path / "p.zip", {f"f{i}.txt": b"x" for i in range(5)})
    with pytest.raises(FetchError, match="entradas"):
        extract_zip(archive, tmp_path / "out", max_files=4)


def test_invalid_zip(tmp_path):
    archive = tmp_path / "p.zip"
    archive.write_bytes(b"not a zip")
    with pytest.raises(FetchError, match="ZIP inválido"):
        fetch_team(make_team(str(archive)), tmp_path / "repos")


def test_invalid_team_id(tmp_path):
    with pytest.raises(FetchError, match="id inválido"):
        fetch_team(make_team(str(tmp_path), team_id="../x"), tmp_path / "repos")


def test_remove_tree_missing_is_noop(tmp_path):
    remove_tree(tmp_path / "nope")


# --- CLI -----------------------------------------------------------------------------------


def write_config(tmp_path: Path, repos: dict[str, str]) -> Path:
    data = {
        "evento": {"nombre": "H", "inicio": "2026-11-01T09:00:00-06:00", "fin": "2026-11-03T18:00:00-06:00"},
        "equipos": [
            {"id": tid, "nombre": "N", "proyecto": "P", "objetivo": "O", "repo": repo}
            for tid, repo in repos.items()
        ],
    }
    path = tmp_path / "equipos.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_cli_fetch_all(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "entrega"
    folder.mkdir()
    (folder / "a.py").write_text("x", encoding="utf-8")
    archive = write_zip(tmp_path / "b.zip", {"b.py": b"y"})
    config = write_config(tmp_path, {"equipo-01": str(folder), "equipo-02": str(archive)})
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert main(["--config", str(config), "fetch"]) == EXIT_OK
    assert (work / "repos" / "equipo-01" / "a.py").exists()
    assert (work / "repos" / "equipo-02" / "b.py").exists()
    out = capsys.readouterr().out
    assert "equipo-01: folder" in out and "equipo-02: zip" in out


def test_cli_fetch_one_team_and_continue_on_error(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "entrega"
    folder.mkdir()
    config = write_config(tmp_path, {"equipo-01": str(tmp_path / "falta"), "equipo-02": str(folder)})
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert main(["--config", str(config), "fetch", "equipo-02"]) == EXIT_OK
    assert not (work / "repos" / "equipo-01").exists()
    assert main(["--config", str(config), "fetch"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "equipo-01: error" in err and "1 de 2" in err
    assert (work / "repos" / "equipo-02").is_dir()


def test_cli_fetch_unknown_team(tmp_path, monkeypatch, capsys):
    config = write_config(tmp_path, {"equipo-01": str(tmp_path)})
    monkeypatch.chdir(tmp_path)
    assert main(["--config", str(config), "fetch", "otro"]) == EXIT_ERROR
    assert "otro" in capsys.readouterr().err


def test_cli_fetch_invalid_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["--config", str(tmp_path / "nope.yml"), "fetch"]) == EXIT_ERROR
    assert "validate" in capsys.readouterr().err


def test_cli_fetch_zip_limit_option(tmp_path, monkeypatch, capsys):
    archive = write_zip(tmp_path / "b.zip", {"b.bin": b"0" * (2 * 1024 * 1024)})
    config = write_config(tmp_path, {"equipo-01": str(archive)})
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert main(["--config", str(config), "fetch", "--max-zip-mb", "1"]) == EXIT_ERROR
    assert "límite" in capsys.readouterr().err
