"""Known open source code with SCANOSS (PB-10, SPEC.md "Detección de plagio").

Runs in two steps so that only fingerprints leave the machine:

1. ``scanoss-py fingerprint`` reads ``repos/<id>`` locally (no network) and writes a WFP file:
   per file an MD5, its size, its relative path and winnowing hashes of its lines.
2. The WFP is filtered and checked here: files under dependency folders (``node_modules``,
   ``vendor``, virtualenvs, ...) are dropped and every remaining line must be a hash line,
   otherwise nothing is sent.
3. ``scanoss-py scan --wfp`` sends only that filtered WFP to the SCANOSS API (osskb.org); this
   process never gets the repository path.

The matches are written to ``reports/<id>/scanoss.json`` (SPEC.md "Formato de resultados"):
per file the component (project), version, licenses and links. Both processes run in an empty
temporary folder, so a ``scanoss.json`` settings file inside the team's repository is not used.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from acr.config import is_valid_team_id

DEFAULT_REPORTS_DIR = Path("reports")
SCANOSS_FILE = "scanoss.json"
FINGERPRINT_TIMEOUT_SECONDS = 10 * 60
SCAN_TIMEOUT_SECONDS = 30 * 60
SCHEMA_VERSION = 1

Runner = Callable[..., subprocess.CompletedProcess]

# Folders holding third-party code (dependencies, virtualenvs, vendored libraries). A file is
# excluded when any folder in its path has one of these names (case-insensitive).
DEPENDENCY_DIRS = frozenset(
    {
        "node_modules",
        "bower_components",
        "jspm_packages",
        "web_modules",
        "vendor",
        "vendors",
        "third_party",
        "third-party",
        "thirdparty",
        "venv",
        ".venv",
        "virtualenv",
        "site-packages",
        "__pypackages__",
        ".tox",
        "pods",
        "carthage",
        ".dart_tool",
        ".pub-cache",
        "elm-stuff",
        ".gradle",
        ".m2",
        ".cargo",
        ".bundle",
    }
)

# Lines allowed in a WFP file: only hashes, sizes and the relative path of each file.
_FILE_LINE = re.compile(r"file=([0-9a-f]{32}),(\d+),(.+)")
_HASH_LINES = (
    re.compile(r"fh2=[0-9a-f]{32}"),
    re.compile(r"hpsm=[0-9a-f]+"),
    re.compile(r"\d+=[0-9a-f]{8}(,[0-9a-f]{8})*"),
)


class ScanossError(Exception):
    """SCANOSS could not be run or its output could not be read; shown to the reviewer."""


@dataclass
class Fingerprints:
    """Filtered WFP ready to send, plus what was left out."""

    wfp: str = ""
    files: list[str] = field(default_factory=list)
    excluded_files: int = 0
    excluded_dirs: set[str] = field(default_factory=set)


def dependency_dir(path: str) -> str | None:
    """Return the dependency folder prefix of ``path`` (``a/node_modules``), or ``None``."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    for index, part in enumerate(parts[:-1]):
        if part.lower() in DEPENDENCY_DIRS:
            return "/".join(parts[: index + 1])
    return None


def _relative(path: str, repo: Path) -> str:
    path = path.replace("\\", "/")
    prefix = repo.resolve().as_posix().rstrip("/") + "/"
    if path.startswith(prefix):
        return path[len(prefix) :]
    return path.lstrip("/")


def filter_wfp(text: str, repo: Path) -> Fingerprints:
    """Drop files under dependency folders and refuse any line that is not a fingerprint."""
    result = Fingerprints()
    kept: list[str] = []
    keep = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        match = _FILE_LINE.fullmatch(line)
        if match:
            path = _relative(match.group(3), repo)
            excluded = dependency_dir(path)
            if excluded is not None:
                keep = False
                result.excluded_files += 1
                result.excluded_dirs.add(excluded)
                continue
            keep = True
            result.files.append(path)
            kept.append(line)
            continue
        if not any(pattern.fullmatch(line) for pattern in _HASH_LINES):
            raise ScanossError(
                f"la huella tiene una línea que no es un hash (línea {number}); no se envía nada"
            )
        if not result.files and not result.excluded_files:
            raise ScanossError("la huella no empieza con 'file='; no se envía nada")
        if keep:
            kept.append(line)
    result.wfp = "\n".join(kept) + "\n" if kept else ""
    return result


def _run(runner: Runner, cmd: list[str], cwd: Path, timeout: int, step: str) -> None:
    try:
        result = runner(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ScanossError("scanoss-py no está instalado o no está en el PATH (ver PB-1)") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScanossError(f"scanoss-py {step} tardó más de {timeout // 60} minutos") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else f"código {result.returncode}"
        raise ScanossError(f"scanoss-py {step} falló: {reason}")


def fingerprint(repo: Path, work: Path, runner: Runner) -> str:
    """Fingerprint ``repo`` locally (no network) and return the WFP text."""
    output = work / "repo.wfp"
    cmd = ["scanoss-py", "fingerprint", "--output", str(output), str(repo.resolve())]
    _run(runner, cmd, work, FINGERPRINT_TIMEOUT_SECONDS, "fingerprint")
    if not output.is_file():
        raise ScanossError("scanoss-py fingerprint no generó la huella")
    return output.read_text(encoding="utf-8", errors="replace")


def scan(wfp: str, work: Path, runner: Runner) -> dict[str, Any]:
    """Send only the filtered WFP to SCANOSS and return its raw results."""
    wfp_file = work / "send.wfp"
    wfp_file.write_text(wfp, encoding="utf-8")
    output = work / "results.json"
    cmd = ["scanoss-py", "scan", "--wfp", str(wfp_file), "--output", str(output)]
    _run(runner, cmd, work, SCAN_TIMEOUT_SECONDS, "scan")
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScanossError("scanoss-py scan no generó resultados") from exc
    except json.JSONDecodeError as exc:
        raise ScanossError("scanoss-py scan no devolvió JSON válido") from exc
    if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
        raise ScanossError("salida inesperada de scanoss-py: se esperaba un objeto por archivo")
    return data


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _percent(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%"))
        except ValueError:
            return None
    return None


def _match(path: str, item: dict[str, Any]) -> dict[str, Any]:
    purls = item.get("purl")
    licenses: list[str] = []
    for license_ in item.get("licenses") or []:
        name = license_.get("name") if isinstance(license_, dict) else None
        if isinstance(name, str) and name and name not in licenses:
            licenses.append(name)
    return {
        "path": path,
        "match": item["id"],
        "matched_percent": _percent(item.get("matched")),
        "lines": _text(item.get("lines")),
        "oss_lines": _text(item.get("oss_lines")),
        "component": _text(item.get("component")),
        "vendor": _text(item.get("vendor")),
        "version": _text(item.get("version")),
        "purl": _text(purls[0]) if isinstance(purls, list) and purls else None,
        "licenses": licenses,
        "url": _text(item.get("url")),
        "file_url": _text(item.get("file_url")),
        "source_file": _text(item.get("file")),
    }


def build_report(team_id: str, prints: Fingerprints, results: dict[str, Any]) -> dict[str, Any]:
    """Matches per file (project, license, link) and totals for the dashboard."""
    matches = []
    for path in sorted(results):
        for item in results[path]:
            if isinstance(item, dict) and item.get("id") in ("file", "snippet"):
                matches.append(_match(path, item))
    matched_files = sorted({m["path"] for m in matches})
    scanned = len(prints.files)
    return {
        "schema_version": SCHEMA_VERSION,
        "team_id": team_id,
        "tool": "scanoss",
        "totals": {
            "files_scanned": scanned,
            "files_matched": len(matched_files),
            "matched_percent": round(100 * len(matched_files) / scanned, 1) if scanned else 0.0,
            "file_matches": sum(m["match"] == "file" for m in matches),
            "snippet_matches": sum(m["match"] == "snippet" for m in matches),
        },
        "excluded": {"files": prints.excluded_files, "dirs": sorted(prints.excluded_dirs)},
        "matches": matches,
    }


def write_scanoss(
    team_id: str,
    repos_dir: Path = Path("repos"),
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    runner: Runner | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Fingerprint ``repos_dir/<id>``, send only the hashes and write ``reports_dir/<id>/scanoss.json``."""
    if not is_valid_team_id(team_id):
        raise ScanossError(f"id inválido ({team_id!r})")
    repo = repos_dir / team_id
    if not repo.is_dir():
        raise ScanossError(f"no existe {repo.as_posix()}; corre 'acr fetch {team_id}'")
    runner = runner or subprocess.run

    with tempfile.TemporaryDirectory(prefix="acr-scanoss-") as tmp:
        work = Path(tmp)
        prints = filter_wfp(fingerprint(repo, work, runner), repo)
        results = scan(prints.wfp, work, runner) if prints.files else {}
    report = build_report(team_id, prints, results)

    out_dir = reports_dir / team_id
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / SCANOSS_FILE
    staging = out_dir / f".{SCANOSS_FILE}.tmp"
    staging.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staging.replace(target)
    return target, report
