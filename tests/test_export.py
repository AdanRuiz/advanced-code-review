"""Tests for the report exports (PB-23, SPEC.md "Exportación de reportes").

md-to-pdf is never run: subprocess is faked and every file lives in a temporary folder.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

from acr import export
from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.export import PDF_FILE, ZIP_FILE, ExportError, write_pdf, write_zip

REPORT = "# Reporte de revisión – Equipo Alfa\n\nTexto.\n"


class FakeMdToPdf:
    """Writes ``<file>.pdf`` next to the Markdown, as md-to-pdf does; records each call."""

    def __init__(self, returncode: int = 0, output: bytes | None = b"%PDF-1.7\nfake\n", stderr: str = ""):
        self.returncode, self.output, self.stderr = returncode, output, stderr
        self.calls: list[dict] = []

    def __call__(self, args, **kwargs):
        markdown = Path(args[-1])
        self.calls.append(
            {
                "args": list(args),
                "kwargs": kwargs,
                "dir_listing": sorted(p.name for p in markdown.parent.iterdir()),
                "text": markdown.read_text(encoding="utf-8"),
            }
        )
        if self.output is not None:
            markdown.with_suffix(".pdf").write_bytes(self.output)
        return subprocess.CompletedProcess(args, self.returncode, "", self.stderr)


def make_report(reports_dir: Path, team_id: str = "equipo-01", text: str = REPORT) -> Path:
    out = reports_dir / team_id
    out.mkdir(parents=True, exist_ok=True)
    path = out / "reporte.md"
    path.write_text(text, encoding="utf-8")
    return path


# --- write_pdf ---------------------------------------------------------------------------------


def test_pdf_is_written_next_to_the_report(tmp_path):
    reports = tmp_path / "reports"
    make_report(reports)
    (reports / "equipo-01" / "trivy.sarif").write_text("{}", encoding="utf-8")
    runner = FakeMdToPdf()
    target = write_pdf("equipo-01", reports, runner=runner)
    assert target == reports / "equipo-01" / PDF_FILE
    assert target.read_bytes().startswith(b"%PDF-")
    assert not list((reports / "equipo-01").glob(".*.tmp"))
    (call,) = runner.calls
    assert call["text"] == REPORT


def test_md_to_pdf_runs_on_an_isolated_copy_without_shell(tmp_path):
    reports = tmp_path / "reports"
    make_report(reports)
    runner = FakeMdToPdf()
    write_pdf("equipo-01", reports, runner=runner)
    (call,) = runner.calls
    args, kwargs = call["args"], call["kwargs"]
    assert Path(args[0]).stem == "md-to-pdf"
    # The local server of md-to-pdf only sees a temporary folder with just this report.
    basedir = Path(args[args.index("--basedir") + 1])
    assert Path(args[-1]) == basedir / "reporte.md"
    assert reports.resolve() not in basedir.resolve().parents
    assert call["dir_listing"] == ["reporte.md"]
    assert not basedir.exists()  # cleaned up
    assert kwargs["cwd"] == basedir
    assert kwargs.get("shell") is not True
    assert kwargs["timeout"] == export.MD_TO_PDF_TIMEOUT_SECONDS


def test_missing_report_points_to_acr_report(tmp_path):
    with pytest.raises(ExportError, match="acr report equipo-01"):
        write_pdf("equipo-01", tmp_path, runner=FakeMdToPdf())


@pytest.mark.parametrize("team_id", ["../x", "Equipo", "a/b", ""])
def test_invalid_id_is_rejected(tmp_path, team_id):
    with pytest.raises(ExportError, match="id inválido"):
        write_pdf(team_id, tmp_path, runner=FakeMdToPdf())


def test_md_to_pdf_not_installed(tmp_path):
    make_report(tmp_path)

    def missing(*args, **kwargs):
        raise FileNotFoundError

    with pytest.raises(ExportError, match="PB-1"):
        write_pdf("equipo-01", tmp_path, runner=missing)


def test_md_to_pdf_timeout(tmp_path):
    make_report(tmp_path)

    def slow(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    with pytest.raises(ExportError, match="tardó"):
        write_pdf("equipo-01", tmp_path, runner=slow)


def test_failure_keeps_the_previous_pdf(tmp_path):
    make_report(tmp_path)
    previous = tmp_path / "equipo-01" / PDF_FILE
    previous.write_bytes(b"%PDF-old")
    with pytest.raises(ExportError, match="Chromium no arrancó"):
        write_pdf("equipo-01", tmp_path, runner=FakeMdToPdf(returncode=1, stderr="x\nChromium no arrancó"))
    with pytest.raises(ExportError, match="PDF válido"):
        write_pdf("equipo-01", tmp_path, runner=FakeMdToPdf(output=b"<html>"))
    with pytest.raises(ExportError, match="PDF válido"):
        write_pdf("equipo-01", tmp_path, runner=FakeMdToPdf(output=None))
    assert previous.read_bytes() == b"%PDF-old"


# --- write_zip ---------------------------------------------------------------------------------


def test_zip_has_reports_of_every_team_and_dolos_csv_only(tmp_path):
    reports = tmp_path / "reports"
    for team_id in ("equipo-01", "equipo-02"):
        make_report(reports, team_id)
        (reports / team_id / PDF_FILE).write_bytes(b"%PDF-1.7")
        (reports / team_id / "megalinter.sarif").write_text("{}", encoding="utf-8")
        (reports / team_id / "analyze.log").write_text("log", encoding="utf-8")
    (reports / "dolos").mkdir()
    (reports / "dolos" / "pairs.csv").write_text("team_a,team_b\n", encoding="utf-8")
    (reports / "status.json").write_text("{}", encoding="utf-8")

    result = write_zip(["equipo-01", "equipo-02"], reports)
    assert result.path == reports / ZIP_FILE
    with zipfile.ZipFile(result.path) as archive:
        names = sorted(archive.namelist())
        assert archive.read("equipo-01/reporte.md") == (reports / "equipo-01" / "reporte.md").read_bytes()
    assert names == [
        "dolos/pairs.csv",
        "equipo-01/reporte.md",
        "equipo-01/reporte.pdf",
        "equipo-02/reporte.md",
        "equipo-02/reporte.pdf",
    ]
    assert result.missing == ["judge-questions.md", "dolos/file-pairs.csv"]
    assert not (reports / f".{ZIP_FILE}.tmp").exists()


def test_zip_lists_missing_reports(tmp_path):
    make_report(tmp_path, "equipo-01")
    result = write_zip(["equipo-01", "equipo-02"], tmp_path)
    assert result.files == ["equipo-01/reporte.md"]
    assert "equipo-01/reporte.pdf" in result.missing and "equipo-02/reporte.md" in result.missing


def test_zip_rejects_invalid_ids(tmp_path):
    with pytest.raises(ExportError, match="id inválido"):
        write_zip(["../etc"], tmp_path)
    assert not (tmp_path / ZIP_FILE).exists()


# --- acr export --------------------------------------------------------------------------------


def write_config(tmp_path: Path, ids: list[str]) -> Path:
    data = {
        "evento": {"nombre": "H", "inicio": "2026-11-01T09:00:00-06:00", "fin": "2026-11-03T18:00:00-06:00"},
        "equipos": [
            {"id": i, "nombre": f"Nombre {i}", "proyecto": "P", "objetivo": "O", "repo": "https://example.org/r.git"}
            for i in ids
        ],
    }
    path = tmp_path / "equipos.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_cli_export_all(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    runner = FakeMdToPdf()
    monkeypatch.setattr(export.subprocess, "run", runner)
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "export"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "equipo-01: PDF -> reports/equipo-01/reporte.pdf" in out
    assert "-> reports/todos.zip" in out
    assert len(runner.calls) == 2
    assert "Nombre equipo-01" in runner.calls[0]["text"]  # regenerated Markdown, then converted
    with zipfile.ZipFile(tmp_path / "reports" / ZIP_FILE) as archive:
        assert "equipo-02/reporte.pdf" in archive.namelist()


def test_cli_export_one_team_still_zips_every_team(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    runner = FakeMdToPdf()
    monkeypatch.setattr(export.subprocess, "run", runner)
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    make_report(tmp_path / "reports", "equipo-01")
    assert main(["--config", str(config), "export", "equipo-02"]) == EXIT_OK
    assert len(runner.calls) == 1
    with zipfile.ZipFile(tmp_path / "reports" / ZIP_FILE) as archive:
        assert {"equipo-01/reporte.md", "equipo-02/reporte.md", "equipo-02/reporte.pdf"} <= set(
            archive.namelist()
        )
    assert "equipo-01/reporte.pdf" in capsys.readouterr().err  # listed as missing

    assert main(["--config", str(config), "export", "equipo-09"]) == EXIT_ERROR
    assert "no hay un equipo con id 'equipo-09'" in capsys.readouterr().err
    assert main(["--config", str(tmp_path / "nope.yml"), "export"]) == EXIT_ERROR


def test_cli_export_without_md_to_pdf_still_writes_markdown_and_zip(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(export.subprocess, "run", missing)
    config = write_config(tmp_path, ["equipo-01"])
    assert main(["--config", str(config), "export"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "equipo-01: error al exportar: md-to-pdf no está instalado" in err
    with zipfile.ZipFile(tmp_path / "reports" / ZIP_FILE) as archive:
        assert archive.namelist() == ["equipo-01/reporte.md", "judge-questions.md"]
