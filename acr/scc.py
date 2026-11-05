"""Language and size recognition with scc (PB-8, SPEC.md "Stack de componentes", paso 1).

Runs ``scc --format json`` on ``repos/<id>`` and writes ``reports/<id>/scc.json`` with the
languages, lines and files of the repository (SPEC.md "Formato de resultados").

scc only reads the files and counts lines; the team's code is never run. The output is
normalised to a small, stable schema so the dashboard (Infinity) does not depend on the
field names of a particular scc version.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from acr.config import is_valid_team_id

DEFAULT_REPORTS_DIR = Path("reports")
SCC_FILE = "scc.json"
SCC_TIMEOUT_SECONDS = 10 * 60
SCHEMA_VERSION = 1

Runner = Callable[..., subprocess.CompletedProcess]

# scc field -> field in scc.json. Every value is a non-negative integer.
_COUNTS = {
    "Count": "files",
    "Lines": "lines",
    "Code": "code",
    "Comment": "comment",
    "Blank": "blank",
    "Bytes": "bytes",
    "Complexity": "complexity",
}


class SccError(Exception):
    """scc could not be run or its output could not be read; shown to the reviewer."""


def run_scc(repo: Path, runner: Runner | None = None) -> list[dict[str, Any]]:
    """Run scc on ``repo`` and return its per-language JSON output."""
    try:
        result = (runner or subprocess.run)(
            ["scc", "--format", "json", str(repo)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SCC_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SccError("scc no está instalado o no está en el PATH (ver PB-1)") from exc
    except subprocess.TimeoutExpired as exc:
        raise SccError(f"scc tardó más de {SCC_TIMEOUT_SECONDS // 60} minutos") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise SccError(f"scc falló: {detail[-1] if detail else f'código {result.returncode}'}")
    try:
        data = json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise SccError("scc no devolvió JSON válido") from exc
    if data is None:  # scc prints null for a folder without recognised files
        return []
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise SccError("salida inesperada de scc: se esperaba una lista de lenguajes")
    return data


def _language(item: dict[str, Any]) -> dict[str, Any]:
    name = item.get("Name")
    if not isinstance(name, str) or not name:
        raise SccError(f"salida inesperada de scc: lenguaje sin nombre ({item!r:.80})")
    language: dict[str, Any] = {"name": name}
    for source, target in _COUNTS.items():
        value = item.get(source, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SccError(f"salida inesperada de scc: {source} inválido para {name}")
        language[target] = value
    return language


def build_report(team_id: str, scc_output: list[dict[str, Any]]) -> dict[str, Any]:
    """Languages sorted by lines of code (largest first) plus totals for the repository."""
    languages = sorted(
        (_language(item) for item in scc_output), key=lambda lang: (-lang["code"], lang["name"])
    )
    totals = {field: sum(lang[field] for lang in languages) for field in _COUNTS.values()}
    return {
        "schema_version": SCHEMA_VERSION,
        "team_id": team_id,
        "tool": "scc",
        "totals": {"languages": len(languages), **totals},
        "languages": languages,
    }


def write_scc(
    team_id: str,
    repos_dir: Path = Path("repos"),
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    runner: Runner | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Run scc on ``repos_dir/<id>`` and write ``reports_dir/<id>/scc.json``."""
    if not is_valid_team_id(team_id):
        raise SccError(f"id inválido ({team_id!r})")
    repo = repos_dir / team_id
    if not repo.is_dir():
        raise SccError(f"no existe {repo.as_posix()}; corre 'acr fetch {team_id}'")
    report = build_report(team_id, run_scc(repo, runner=runner))

    out_dir = reports_dir / team_id
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / SCC_FILE
    staging = out_dir / f".{SCC_FILE}.tmp"
    staging.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staging.replace(target)
    return target, report
