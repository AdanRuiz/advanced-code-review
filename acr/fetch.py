"""Fetch each team's repository into ``repos/<id>`` (PB-6, SPEC.md "Entrada" and "Entorno").

A team's ``repo`` is a git URL, a local folder or a ZIP file. The code is only obtained and
stored; it is never executed. Every fetch is done into a temporary folder next to the target
and only replaces ``repos/<id>`` once it succeeded, so a failed fetch never leaves a half
copy behind. Fetched files are marked read-only.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from acr.config import Team, is_valid_team_id

DEFAULT_REPOS_DIR = Path("repos")
DEFAULT_MAX_ZIP_BYTES = 1024 * 1024 * 1024  # 1 GiB uncompressed
DEFAULT_MAX_ZIP_FILES = 100_000
GIT_TIMEOUT_SECONDS = 30 * 60

# Only network transports. ``ext::`` and other remote helpers can run commands and
# ``file://`` would read arbitrary local paths, so they are rejected.
_URL_RE = re.compile(r"^(https?|ssh|git)://[^\s]+$", re.IGNORECASE)
# scp-like syntax: user@host:path (requires the user so "C:/..." is never taken for it).
_SCP_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^\s]+$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")
_BRANCH_FORBIDDEN_RE = re.compile(r"[\s~^:?*\[\\\x00-\x1f\x7f]|\.\.|@\{")

SourceKind = Literal["git", "folder", "zip"]
Runner = Callable[..., subprocess.CompletedProcess]


class FetchError(Exception):
    """A repository could not be fetched; the message is shown to the reviewer."""


@dataclass(frozen=True)
class FetchResult:
    team_id: str
    kind: SourceKind
    path: Path


def is_git_url(repo: str) -> bool:
    return bool(_URL_RE.match(repo) or _SCP_RE.match(repo))


def classify_source(repo: str) -> SourceKind:
    """Tell whether ``repo`` is a git URL, a ZIP file or a folder (local paths must exist)."""
    if is_git_url(repo):
        return "git"
    if "://" in repo or repo.startswith("-"):
        raise FetchError(f"origen no permitido ({repo!r}): usa https://, ssh://, git:// o user@host:ruta")
    path = Path(repo)
    if path.suffix.lower() == ".zip":
        if not path.is_file():
            raise FetchError(f"no existe el archivo ZIP {repo}")
        return "zip"
    if not path.is_dir():
        raise FetchError(f"no existe la carpeta {repo}")
    return "folder"


def _validate_ref(team: Team) -> None:
    if team.rama is not None and (
        team.rama.startswith(("-", "/")) or team.rama.endswith((".", "/", ".lock"))
        or _BRANCH_FORBIDDEN_RE.search(team.rama)
    ):
        raise FetchError(f"rama inválida ({team.rama!r})")
    if team.commit is not None and not _COMMIT_RE.fullmatch(team.commit):
        raise FetchError(f"commit inválido ({team.commit!r}): debe ser un hash hexadecimal")


# --- git -----------------------------------------------------------------------------------


def _run_git(runner: Runner, action: str, args: list[str]) -> None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = runner(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FetchError("git no está instalado o no está en el PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"git tardó más de {GIT_TIMEOUT_SECONDS // 60} minutos") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else f"código {result.returncode}"
        raise FetchError(f"git {action} falló: {reason}")


def clone_git(team: Team, dest: Path, runner: Runner = subprocess.run) -> None:
    """Clone with full history (PB-7 needs it), then pin ``commit`` if given.

    Symlinks are checked out as plain files so nothing in the clone points outside it.
    """
    args = [
        "-c", "core.symlinks=false",
        "-c", "protocol.ext.allow=never",
        "-c", "protocol.file.allow=never",
        "clone", "--no-recurse-submodules",
    ]
    if team.rama is not None:
        args.append(f"--branch={team.rama}")
    args += ["--", team.repo, str(dest)]
    _run_git(runner, "clone", args)
    if team.commit is not None:
        _run_git(
            runner,
            "checkout",
            ["-C", str(dest), "-c", "core.symlinks=false", "checkout", "--detach", team.commit, "--"],
        )


# --- folder --------------------------------------------------------------------------------


def copy_folder(source: Path, dest: Path) -> None:
    """Copy a local folder; symbolic links are skipped so nothing outside it is copied."""
    source = source.resolve()
    target = dest.resolve()
    if target == source or source in target.parents:
        raise FetchError(f"la carpeta {source} contiene el destino {dest}")

    def ignore_links(directory: str, names: list[str]) -> list[str]:
        return [name for name in names if os.path.islink(os.path.join(directory, name))]

    try:
        shutil.copytree(source, dest, symlinks=False, ignore=ignore_links)
    except (OSError, shutil.Error) as exc:
        raise FetchError(f"no se pudo copiar la carpeta: {exc}") from exc


# --- ZIP -----------------------------------------------------------------------------------


def _safe_member_path(name: str) -> PurePosixPath:
    """Reject absolute paths, drive letters and ``..`` (zip slip)."""
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part == ".." for part in path.parts)
        or "\x00" in normalized
    ):
        raise FetchError(f"ZIP con ruta peligrosa: {name!r}")
    return path


def extract_zip(
    source: Path,
    dest: Path,
    max_bytes: int = DEFAULT_MAX_ZIP_BYTES,
    max_files: int = DEFAULT_MAX_ZIP_FILES,
) -> None:
    """Extract a ZIP with zip-slip protection and limits on total size and number of files.

    The whole archive is checked before anything is written. Symlink entries are rejected.
    Sizes are enforced while writing too, so a ZIP that lies about its sizes is stopped.
    """
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise FetchError(f"ZIP inválido: {exc}") from exc

    with archive:
        members = archive.infolist()
        if len(members) > max_files:
            raise FetchError(f"el ZIP tiene {len(members)} entradas; el límite es {max_files}")
        declared = 0
        for info in members:
            _safe_member_path(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise FetchError(f"ZIP con enlace simbólico: {info.filename!r}")
            declared += info.file_size
        if declared > max_bytes:
            raise FetchError(f"el ZIP descomprimido ocupa {declared} bytes; el límite es {max_bytes}")

        root = dest.resolve()
        root.mkdir(parents=True, exist_ok=True)
        written = 0
        for info in members:
            target = (root / _safe_member_path(info.filename)).resolve()
            if target != root and root not in target.parents:
                raise FetchError(f"ZIP con ruta peligrosa: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as src, open(target, "wb") as out:
                    while chunk := src.read(1024 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            raise FetchError(f"el ZIP descomprimido supera el límite de {max_bytes} bytes")
                        out.write(chunk)
            except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
                raise FetchError(f"no se pudo extraer {info.filename!r}: {exc}") from exc


# --- read-only and replacement -------------------------------------------------------------


def make_read_only(root: Path) -> None:
    """Remove write permission from every fetched file (SPEC.md "Entorno": solo lectura)."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                mode = os.stat(path).st_mode
                os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def remove_tree(path: Path) -> None:
    """Delete a fetched tree, including read-only files."""
    if not path.exists() and not path.is_symlink():
        return

    def make_writable_and_retry(func, target, _exc):
        os.chmod(target, stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR)
        func(target)

    shutil.rmtree(path, onexc=make_writable_and_retry)


def fetch_team(
    team: Team,
    repos_dir: Path = DEFAULT_REPOS_DIR,
    *,
    max_zip_bytes: int = DEFAULT_MAX_ZIP_BYTES,
    runner: Runner = subprocess.run,
) -> FetchResult:
    """Fetch one team into ``repos_dir/<id>``, replacing a previous copy only on success."""
    if not is_valid_team_id(team.id):
        raise FetchError(f"id inválido ({team.id!r})")
    kind = classify_source(team.repo)
    if kind != "git" and (team.rama is not None or team.commit is not None):
        raise FetchError("rama y commit solo aplican a una URL git; quítalos o usa la URL")
    _validate_ref(team)

    repos_dir.mkdir(parents=True, exist_ok=True)
    final = repos_dir / team.id
    staging = repos_dir / f".{team.id}.tmp"
    remove_tree(staging)
    try:
        if kind == "git":
            clone_git(team, staging, runner=runner)
        elif kind == "zip":
            extract_zip(Path(team.repo), staging, max_bytes=max_zip_bytes)
        else:
            copy_folder(Path(team.repo), staging)
        make_read_only(staging)
        remove_tree(final)
        staging.rename(final)
    except BaseException:
        remove_tree(staging)
        raise
    return FetchResult(team_id=team.id, kind=kind, path=final)
