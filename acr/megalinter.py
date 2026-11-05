"""Quality, Semgrep, Betterleaks and Trivy in one MegaLinter run (PB-9, SPEC.md "MegaLinter").

Runs the MegaLinter container (v10 or later) once per team with ``podman`` (or ``docker``):

- ``repos/<id>`` is mounted read-only as the workspace; the team's code is only read by the
  linters, never run or modified.
- the project's ``.mega-linter.yml`` is mounted read-only and forced with ``MEGALINTER_CONFIG``,
  so a configuration file inside the team's repository is ignored.
- reports are written outside the repository, to a staging folder under ``reports/<id>/``.
- nothing else from the host is mounted, and the container runs without capabilities, without
  privilege escalation and with process and memory limits (PB-40).

When MegaLinter produced its reports, ``reports/<id>/`` gets ``megalinter.sarif``,
``megalinter.json`` and ``megalinter.md``, plus ``trivy.sarif`` and ``semgrep.sarif`` (SPEC.md
"Formato de resultados") when those linters ran, and the full output in ``megalinter/``.
Otherwise the previous reports are kept.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from acr.config import is_valid_team_id
from acr.fetch import remove_tree

DEFAULT_REPORTS_DIR = Path("reports")
DEFAULT_IMAGE = "docker.io/oxsecurity/megalinter:v10"
DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent.parent / ".mega-linter.yml"
DEFAULT_TIMEOUT_SECONDS = 60 * 60
ENGINES = ("podman", "docker")

# Container hardening (PB-40, SPEC.md "Entorno, seguridad y privacidad"): no Linux capabilities,
# no privilege escalation (setuid binaries) and bounded processes and memory, so a linter that
# loads executable configuration from the team's repository has as little reach as possible.
MEMORY_LIMIT = "8g"
PIDS_LIMIT = 4096
HARDENING_OPTIONS = (
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    f"--pids-limit={PIDS_LIMIT}",
    f"--memory={MEMORY_LIMIT}",
    f"--memory-swap={MEMORY_LIMIT}",
)

# Paths inside the container.
WORKSPACE = "/tmp/lint"
CONFIG_TARGET = "/acr-config/.mega-linter.yml"
REPORTS_TARGET = "/acr-reports"

RAW_DIR = "megalinter"
STAGING_DIR = ".megalinter.tmp"
# Aggregated reports (file names fixed in .mega-linter.yml), all required.
MAIN_REPORTS = ("megalinter.sarif", "megalinter.json", "megalinter.md")
# Per-linter SARIF copied for the Security page: MegaLinter linter name -> file in reports/<id>/.
LINTER_SARIF = {"REPOSITORY_TRIVY": "trivy.sarif", "REPOSITORY_SEMGREP": "semgrep.sarif"}

Runner = Callable[..., subprocess.CompletedProcess]
Which = Callable[[str], str | None]


class MegaLinterError(Exception):
    """MegaLinter could not be run or did not produce its reports; shown to the reviewer."""


@dataclass(frozen=True)
class MegaLinterResult:
    returncode: int
    files: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def find_engine(preferred: str | None = None, which: Which = shutil.which) -> str:
    """Return the container engine to use: ``preferred`` if given, else podman, else docker."""
    candidates = (preferred,) if preferred else ENGINES
    for engine in candidates:
        if engine not in ENGINES:
            raise MegaLinterError(f"motor de contenedores no soportado ({engine!r})")
        if which(engine):
            return engine
    raise MegaLinterError(f"no se encontró {' ni '.join(candidates)} en el PATH (ver PB-1)")


def container_name(team_id: str) -> str:
    return f"acr-megalinter-{team_id}"


def _bind(source: Path, target: str, read_only: bool) -> str:
    source_text = str(source.resolve())
    if "," in source_text or "=" in source_text:
        raise MegaLinterError(f"la ruta {source_text!r} no puede contener ',' ni '='")
    spec = f"type=bind,source={source_text},target={target}"
    return spec + ",readonly=true" if read_only else spec


def build_command(
    engine: str, image: str, team_id: str, repo: Path, output: Path, config_file: Path
) -> list[str]:
    """Hardened container command line.

    The only host paths mounted are the repository and the configuration (read-only) and the
    reports folder (outside the repository).
    """
    return [
        engine,
        "run",
        "--rm",
        "--name",
        container_name(team_id),
        *HARDENING_OPTIONS,
        "--mount",
        _bind(repo, WORKSPACE, read_only=True),
        "--mount",
        _bind(config_file, CONFIG_TARGET, read_only=True),
        "--mount",
        _bind(output, REPORTS_TARGET, read_only=False),
        "-e",
        f"DEFAULT_WORKSPACE={WORKSPACE}",
        "-e",
        f"MEGALINTER_CONFIG={CONFIG_TARGET}",
        "-e",
        f"REPORT_OUTPUT_FOLDER={REPORTS_TARGET}",
        image,
    ]


def _last_line(text: str | None) -> str | None:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1].strip() if lines else None


def _run_container(cmd: list[str], engine: str, team_id: str, runner: Runner, timeout: int) -> int:
    try:
        result = runner(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise MegaLinterError(f"{engine} no está instalado o no está en el PATH (ver PB-1)") from exc
    except subprocess.TimeoutExpired as exc:
        # The engine client was killed; stop the container too so it does not keep running.
        try:
            runner(
                [engine, "rm", "--force", container_name(team_id)],
                capture_output=True, text=True, timeout=120, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        raise MegaLinterError(f"MegaLinter tardó más de {timeout // 60} minutos") from exc
    if result.returncode not in (0, 1):
        # MegaLinter exits 1 when a linter found errors; anything else is a failed run.
        detail = _last_line(result.stderr) or _last_line(result.stdout) or f"código {result.returncode}"
        raise MegaLinterError(f"MegaLinter falló: {detail}")
    return result.returncode


def _check_reports(output: Path) -> None:
    missing = [name for name in MAIN_REPORTS if not (output / name).is_file()]
    if missing:
        raise MegaLinterError(f"MegaLinter no generó {', '.join(missing)}")
    try:
        data = json.loads((output / "megalinter.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MegaLinterError("megalinter.json no es JSON válido") from exc
    if not isinstance(data, dict):
        raise MegaLinterError("megalinter.json no tiene el formato esperado")


def _linter_sarif(output: Path) -> dict[str, Path]:
    """Per-linter SARIF files written by MegaLinter in ``sarif/`` (matched by linter name)."""
    found: dict[str, Path] = {}
    sarif_dir = output / "sarif"
    if not sarif_dir.is_dir():
        return found
    for path in sorted(sarif_dir.iterdir()):
        if path.is_file() and path.suffix.lower() == ".sarif":
            linter = path.stem.upper()
            if linter in LINTER_SARIF:
                found[LINTER_SARIF[linter]] = path
    return found


def _publish(source: Path, target: Path) -> None:
    staging = target.with_name(f".{target.name}.tmp")
    shutil.copyfile(source, staging)
    staging.replace(target)


def run_megalinter(
    team_id: str,
    repos_dir: Path = Path("repos"),
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    *,
    engine: str | None = None,
    image: str = DEFAULT_IMAGE,
    config_file: Path = DEFAULT_CONFIG_FILE,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner | None = None,
    which: Which = shutil.which,
) -> MegaLinterResult:
    """Run MegaLinter on ``repos_dir/<id>`` and write its reports to ``reports_dir/<id>/``."""
    if not is_valid_team_id(team_id):
        raise MegaLinterError(f"id inválido ({team_id!r})")
    repo = repos_dir / team_id
    if not repo.is_dir():
        raise MegaLinterError(f"no existe {repo.as_posix()}; corre 'acr fetch {team_id}'")
    if not config_file.is_file():
        raise MegaLinterError(f"no existe la configuración {config_file.as_posix()}")
    chosen = find_engine(engine, which)

    out_dir = reports_dir / team_id
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / STAGING_DIR
    remove_tree(staging)
    staging.mkdir()
    try:
        cmd = build_command(chosen, image, team_id, repo, staging, config_file)
        returncode = _run_container(cmd, chosen, team_id, runner or subprocess.run, timeout)
        _check_reports(staging)
        sarif = _linter_sarif(staging)

        raw = out_dir / RAW_DIR
        remove_tree(raw)
        staging.replace(raw)
    finally:
        remove_tree(staging)

    files = []
    for name in MAIN_REPORTS:
        _publish(raw / name, out_dir / name)
        files.append(out_dir / name)
    missing = []
    for name in LINTER_SARIF.values():
        target = out_dir / name
        if name in sarif:
            _publish(raw / "sarif" / sarif[name].name, target)
            files.append(target)
        else:
            target.unlink(missing_ok=True)  # never keep a stale result from a previous run
            missing.append(name)
    return MegaLinterResult(returncode=returncode, files=files, missing=missing)
