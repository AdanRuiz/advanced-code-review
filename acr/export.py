"""Report exports: PDF per team and a ZIP with every team (PB-23, SPEC.md "Exportación de reportes").

Grafana's built-in PDF export is paid, so the pipeline writes the files beforehand and the navbar
buttons only download them from the local file server:

- ``reports/<id>/reporte.pdf``: ``reports/<id>/reporte.md`` (PB-14) converted with md-to-pdf.
- ``reports/todos.zip``: ``reporte.md`` and ``reporte.pdf`` of every team, ``judge-questions.md``
  (PB-59) and the Dolos CSV.

md-to-pdf renders the Markdown in a headless browser and serves its ``--basedir`` over a local
HTTP server while it converts. It is run on a copy of the Markdown in an empty temporary folder,
so that server can only ever see that one report (never ``reports/`` or the teams' code). The
report escapes every text that comes from a team repository (acr.report), so it cannot inject
HTML, images or front matter into the PDF.

Every file is written to a staging name and moved into place when complete, so a failed export
keeps the previous one.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from acr.config import is_valid_team_id
from acr.report import DEFAULT_REPORTS_DIR, JUDGE_QUESTIONS_FILE, REPORT_FILE

PDF_FILE = "reporte.pdf"
ZIP_FILE = "todos.zip"
DOLOS_FILES = ("dolos/pairs.csv", "dolos/file-pairs.csv")
MD_TO_PDF_TIMEOUT_SECONDS = 5 * 60

Runner = Callable[..., subprocess.CompletedProcess]


class ExportError(Exception):
    """An export could not be written; shown to the reviewer."""


def _md_to_pdf_command() -> str:
    # npm installs ``md-to-pdf.cmd`` on Windows, which CreateProcess does not find without its suffix.
    return shutil.which("md-to-pdf") or "md-to-pdf"


def write_pdf(
    team_id: str,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    runner: Runner | None = None,
) -> Path:
    """Convert ``reports_dir/<id>/reporte.md`` to ``reports_dir/<id>/reporte.pdf`` with md-to-pdf."""
    if not is_valid_team_id(team_id):
        raise ExportError(f"id inválido ({team_id!r})")
    out_dir = reports_dir / team_id
    source = out_dir / REPORT_FILE
    if not source.is_file():
        raise ExportError(f"no existe {source.as_posix()}; corre 'acr report {team_id}'")

    # ignore_cleanup_errors: after a timeout the md-to-pdf child (node/Chromium) may still hold files.
    with tempfile.TemporaryDirectory(prefix="acr-pdf-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        markdown = work / REPORT_FILE
        shutil.copyfile(source, markdown)
        # Output goes to an unnamed temp file, not a pipe: on Windows the timeout kills only
        # md-to-pdf.cmd, and its node child kept the pipes open, so the run never returned.
        with tempfile.TemporaryFile() as output:
            try:
                result = (runner or subprocess.run)(
                    [_md_to_pdf_command(), "--basedir", str(work), str(markdown)],
                    cwd=work,
                    # md-to-pdf's CLI first waits for stdin to end (get-stdin); an inherited open
                    # stdin (background shells, services) made it wait forever.
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    timeout=MD_TO_PDF_TIMEOUT_SECONDS,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise ExportError("md-to-pdf no está instalado o no está en el PATH (ver PB-1)") from exc
            except subprocess.TimeoutExpired as exc:
                minutes = MD_TO_PDF_TIMEOUT_SECONDS // 60
                raise ExportError(f"md-to-pdf tardó más de {minutes} minutos") from exc
            output.seek(0)
            captured = output.read().decode("utf-8", errors="replace")
        if result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else ""
            detail = (stderr or captured).strip().splitlines()
            raise ExportError(f"md-to-pdf falló: {detail[-1] if detail else f'código {result.returncode}'}")
        produced = markdown.with_suffix(".pdf")
        try:
            with produced.open("rb") as handle:
                is_pdf = handle.read(5) == b"%PDF-"
        except OSError:
            is_pdf = False
        if not is_pdf:
            raise ExportError(f"md-to-pdf no generó un PDF válido para {team_id}")
        target = out_dir / PDF_FILE
        staging = out_dir / f".{PDF_FILE}.tmp"
        shutil.copyfile(produced, staging)
        staging.replace(target)
    return target


@dataclass
class ZipResult:
    path: Path
    files: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def write_zip(team_ids: Iterable[str], reports_dir: Path = DEFAULT_REPORTS_DIR) -> ZipResult:
    """Write ``reports_dir/todos.zip`` with every team's report (MD and PDF) and the Dolos CSV.

    Only these known files are added, under ``<id>/`` and ``dolos/``; anything else in
    ``reports/`` (SARIF, logs, locks) stays out. Missing files are listed, not fatal.
    """
    wanted: list[str] = []
    for team_id in team_ids:
        if not is_valid_team_id(team_id):
            raise ExportError(f"id inválido ({team_id!r})")
        wanted += [f"{team_id}/{REPORT_FILE}", f"{team_id}/{PDF_FILE}"]
    wanted += [JUDGE_QUESTIONS_FILE, *DOLOS_FILES]

    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / ZIP_FILE
    staging = reports_dir / f".{ZIP_FILE}.tmp"
    result = ZipResult(path=target)
    with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in wanted:
            path = reports_dir / name
            if path.is_file() and not path.is_symlink():
                archive.write(path, arcname=name)
                result.files.append(name)
            else:
                result.missing.append(name)
    staging.replace(target)
    return result
