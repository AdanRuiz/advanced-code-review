"""Similarity between teams with Dolos (PB-11, SPEC.md "Detección de plagio").

Dolos runs once over every fetched repository together (SPEC.md "Arquitectura y flujo", step 5).
Dolos compares files of a single language, so the source files of ``repos/<id>`` are grouped by
language (by extension) and Dolos runs once per language that at least two teams use. Files in
dependency folders (the same list as SCANOSS) and in ``.git`` are left out; files of languages
Dolos does not support are not compared and are counted in the summary (SPEC.md "Riesgos y
pendientes").

Dolos only reads the files: the code is never executed. It runs with ``--output-format csv``, so
it does not start its local web server and nothing leaves the machine. The files to compare go in
an info CSV (``filename``, ``label`` = team id), which avoids command-line length limits.

Results, in ``reports/dolos/`` (SPEC.md "Formato de resultados"):

- ``pairs.csv``: one row per pair of teams, with the highest similarity between any two of their
  files (0 to 1, like Dolos), the number of similar file pairs and the most similar files.
- ``file-pairs.csv``: every pair of files of different teams reported by Dolos.
- ``dolos.json``: teams and languages compared, and files left out.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from acr.config import is_valid_team_id
from acr.scanoss import dependency_dir

DEFAULT_REPORTS_DIR = Path("reports")
DOLOS_DIR = "dolos"
PAIRS_FILE = "pairs.csv"
FILE_PAIRS_FILE = "file-pairs.csv"
SUMMARY_FILE = "dolos.json"
TIMEOUT_SECONDS = 30 * 60
SCHEMA_VERSION = 1

Runner = Callable[..., subprocess.CompletedProcess]

# File extension -> Dolos language name (``dolos run --language``).
LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "c-sharp",
    ".php": "php",
    ".sh": "bash",
    ".bash": "bash",
    ".r": "r",
    ".scala": "scala",
    ".sql": "sql",
    ".elm": "elm",
    ".groovy": "groovy",
    ".v": "verilog",
}

PAIR_COLUMNS = (
    "team_a",
    "team_b",
    "similarity",
    "file_pairs",
    "longest_fragment",
    "total_overlap",
    "file_a",
    "file_b",
)
FILE_PAIR_COLUMNS = (
    "team_a",
    "file_a",
    "team_b",
    "file_b",
    "language",
    "similarity",
    "longest_fragment",
    "total_overlap",
)


class DolosError(Exception):
    """Dolos could not be run or its output could not be read; shown to the reviewer."""


@dataclass
class SourceFiles:
    """Files to compare, grouped by Dolos language, and what was left out."""

    by_language: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    unsupported: Counter = field(default_factory=Counter)
    excluded_dependency_files: int = 0


@dataclass(frozen=True)
class FilePair:
    team_a: str
    file_a: str
    team_b: str
    file_b: str
    language: str
    similarity: float
    longest_fragment: int
    total_overlap: int


def collect_files(repos: dict[str, Path]) -> SourceFiles:
    """Walk each team repository and group its source files (repo-relative, POSIX) by language."""
    result = SourceFiles()
    for team_id in sorted(repos):
        root = repos[team_id]
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if name != ".git")
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                if dependency_dir(relative) is not None:
                    result.excluded_dependency_files += 1
                    continue
                suffix = path.suffix.lower()
                language = LANGUAGES.get(suffix)
                if language is None:
                    result.unsupported[suffix or "(sin extensión)"] += 1
                    continue
                result.by_language.setdefault(language, {}).setdefault(team_id, []).append(relative)
    return result


def _dolos_command() -> str:
    # npm installs ``dolos.cmd`` on Windows, which CreateProcess does not find without its suffix.
    return shutil.which("dolos") or "dolos"


def _write_info_csv(target: Path, work: Path, repos: dict[str, Path], files: dict[str, list[str]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["filename", "label"])
    for team_id in sorted(files):
        root = repos[team_id].resolve()
        for relative in files[team_id]:
            try:
                filename = os.path.relpath(root / relative, work)
            except ValueError as exc:  # Windows: different drives
                raise DolosError("repos/ y reports/ deben estar en la misma unidad") from exc
            writer.writerow([Path(filename).as_posix(), team_id])
    target.write_text(buffer.getvalue(), encoding="utf-8")


def _number(value: str | None, cast: Callable[[str], Any], default: Any) -> Any:
    try:
        return cast(value) if value not in (None, "") else default
    except ValueError:
        return default


def _owner(path_text: str, work: Path, roots: dict[str, Path]) -> tuple[str, str] | None:
    path = Path(path_text)
    if not path.is_absolute():
        path = work / path
    path = Path(os.path.normpath(path))
    for team_id, root in roots.items():
        try:
            return team_id, path.relative_to(root).as_posix()
        except ValueError:
            continue
    return None


def parse_pairs(text: str, language: str, work: Path, repos: dict[str, Path]) -> list[FilePair]:
    """Read Dolos' ``pairs.csv`` and keep the pairs of files that belong to different teams."""
    reader = csv.DictReader(io.StringIO(text))
    required = {"leftFilePath", "rightFilePath", "similarity"}
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise DolosError(f"salida inesperada de Dolos ({language}): faltan columnas de pairs.csv")
    roots = {team_id: Path(os.path.normpath(root.resolve())) for team_id, root in repos.items()}
    pairs: list[FilePair] = []
    for row in reader:
        left = _owner(row["leftFilePath"], work, roots)
        right = _owner(row["rightFilePath"], work, roots)
        if left is None or right is None:
            raise DolosError(f"salida inesperada de Dolos ({language}): archivo fuera de repos/")
        if left[0] == right[0]:
            continue
        if right[0] < left[0]:
            left, right = right, left
        similarity = _number(row["similarity"], float, None)
        if similarity is None:
            raise DolosError(f"salida inesperada de Dolos ({language}): similitud no numérica")
        pairs.append(
            FilePair(
                team_a=left[0],
                file_a=left[1],
                team_b=right[0],
                file_b=right[1],
                language=language,
                similarity=similarity,
                longest_fragment=_number(row.get("longestFragment"), int, 0),
                total_overlap=_number(row.get("totalOverlap"), int, 0),
            )
        )
    return pairs


def run_dolos(
    language: str,
    files: dict[str, list[str]],
    repos: dict[str, Path],
    work: Path,
    runner: Runner,
    timeout: int = TIMEOUT_SECONDS,
) -> list[FilePair]:
    """Run Dolos over the files of one language of every team and return cross-team pairs."""
    info = work / f"{language}.csv"
    output = work / f"out-{language}"
    _write_info_csv(info, work, repos, files)
    cmd = [
        _dolos_command(),
        "run",
        "--language",
        language,
        "--output-format",
        "csv",
        "--output-destination",
        str(output),
        str(info),
    ]
    try:
        result = runner(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=work,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DolosError("Dolos no está instalado o no está en el PATH (ver PB-1)") from exc
    except subprocess.TimeoutExpired as exc:
        raise DolosError(f"Dolos ({language}) tardó más de {timeout // 60} minutos") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else f"código {result.returncode}"
        raise DolosError(f"Dolos ({language}) falló: {reason}")
    try:
        text = (output / PAIRS_FILE).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DolosError(f"Dolos ({language}) no generó pairs.csv") from exc
    return parse_pairs(text, language, work, repos)


def team_pairs(team_ids: Iterable[str], pairs: Iterable[FilePair]) -> list[dict[str, Any]]:
    """One row per pair of teams (all of them, for the matrix): highest file similarity and totals."""
    rows = {
        (a, b): {
            "team_a": a,
            "team_b": b,
            "similarity": 0.0,
            "file_pairs": 0,
            "longest_fragment": 0,
            "total_overlap": 0,
            "file_a": "",
            "file_b": "",
        }
        for a, b in combinations(sorted(set(team_ids)), 2)
    }
    for pair in pairs:
        row = rows[(pair.team_a, pair.team_b)]
        row["file_pairs"] += 1
        row["total_overlap"] += pair.total_overlap
        row["longest_fragment"] = max(row["longest_fragment"], pair.longest_fragment)
        if pair.similarity > row["similarity"] or not row["file_a"]:
            row["similarity"] = pair.similarity
            row["file_a"], row["file_b"] = pair.file_a, pair.file_b
    for row in rows.values():
        row["similarity"] = round(row["similarity"], 4)
    return sorted(rows.values(), key=lambda r: (-r["similarity"], r["team_a"], r["team_b"]))


def _csv_text(columns: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _write(target: Path, text: str) -> None:
    staging = target.with_name(f".{target.name}.tmp")
    staging.write_text(text, encoding="utf-8")
    staging.replace(target)


@dataclass
class DolosResult:
    pairs_path: Path
    team_pairs: list[dict[str, Any]]
    summary: dict[str, Any]


def write_dolos(
    team_ids: Iterable[str],
    repos_dir: Path = Path("repos"),
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    runner: Runner | None = None,
) -> DolosResult:
    """Run Dolos once over ``repos_dir/<id>`` of every team and write ``reports_dir/dolos/``.

    Every team must already be fetched; the caller decides what to do with missing ones.
    """
    team_ids = sorted(set(team_ids))
    for team_id in team_ids:
        if not is_valid_team_id(team_id):
            raise DolosError(f"id inválido ({team_id!r})")
        if not (repos_dir / team_id).is_dir():
            raise DolosError(f"no existe {(repos_dir / team_id).as_posix()}; corre 'acr fetch {team_id}'")
    if len(team_ids) < 2:
        raise DolosError("Dolos necesita al menos dos equipos obtenidos para compararlos")
    runner = runner or subprocess.run

    repos = {team_id: repos_dir / team_id for team_id in team_ids}
    sources = collect_files(repos)
    compared = {lang: files for lang, files in sources.by_language.items() if len(files) >= 2}

    out_dir = reports_dir / DOLOS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    file_pairs: list[FilePair] = []
    with tempfile.TemporaryDirectory(prefix=".acr-dolos-", dir=out_dir) as tmp:
        work = Path(tmp).resolve()
        for language in sorted(compared):
            file_pairs.extend(run_dolos(language, compared[language], repos, work, runner))
    file_pairs.sort(key=lambda p: (-p.similarity, p.team_a, p.team_b, p.file_a, p.file_b))

    rows = team_pairs(team_ids, file_pairs)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "tool": "dolos",
        "teams": team_ids,
        "languages": [
            {
                "language": language,
                "compared": language in compared,
                "files": {team_id: len(paths) for team_id, paths in sorted(files.items())},
            }
            for language, files in sorted(sources.by_language.items())
        ],
        "excluded": {
            "dependency_files": sources.excluded_dependency_files,
            "unsupported_files": sum(sources.unsupported.values()),
            "unsupported_extensions": dict(sorted(sources.unsupported.items())),
        },
        "totals": {
            "team_pairs": len(rows),
            "team_pairs_with_matches": sum(row["file_pairs"] > 0 for row in rows),
            "file_pairs": len(file_pairs),
        },
    }

    _write(out_dir / FILE_PAIRS_FILE, _csv_text(FILE_PAIR_COLUMNS, (vars(p) for p in file_pairs)))
    _write(out_dir / SUMMARY_FILE, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    pairs_path = out_dir / PAIRS_FILE
    _write(pairs_path, _csv_text(PAIR_COLUMNS, rows))
    return DolosResult(pairs_path, rows, summary)
