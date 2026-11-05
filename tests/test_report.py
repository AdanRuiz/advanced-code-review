import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.config import Event, Team
from acr.report import (
    REPORT_FILE,
    ReportError,
    build_report,
    fixable_critical,
    link,
    md,
    sarif_findings,
    vulnerabilities,
    write_report,
)

EVENT = Event(
    "Hackathon 2026",
    datetime(2026, 11, 1, 9, tzinfo=UTC),
    datetime(2026, 11, 3, 18, tzinfo=UTC),
)
TEAM = Team("equipo-01", "Equipo Alfa", "Proyecto Alfa", "Ayudar a estudiantes", "https://example.org/a.git")
NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
SECRET = "sk-THIS-IS-A-SECRET-VALUE-123"

TEMPLATE_HEADINGS = [
    "# Reporte de revisión – Equipo Alfa – Proyecto Alfa",
    "## Resumen",
    "- Lenguajes y tamaño (scc):",
    "- Enlace al dashboard (Grafana):",
    "- Hallazgos críticos:",
    "## Factibilidad técnica",
    "## Calidad del código",
    "## Seguridad",
    "- CVE críticas/altas con corrección disponible:",
    "- Código inseguro (CWE / OWASP Top 10):",
    "- Secretos expuestos:",
    "## Integración responsable de IA",
    "## Plagio",
    "- Código externo (SCANOSS):",
    "- Entre equipos (Dolos):",
    "- Ideas en la web:",
    "- Historial de git:",
    "## Recomendaciones al equipo",
]


def run(tool, results, rules=None):
    return {"tool": {"driver": {"name": tool, "rules": rules or []}}, "results": results}


def result(rule, uri, line, level="error", text="msg"):
    return {
        "ruleId": rule,
        "level": level,
        "message": {"text": text},
        "locations": [
            {"physicalLocation": {"artifactLocation": {"uri": uri}, "region": {"startLine": line}}}
        ],
    }


def trivy_result(cve, pkg, installed, fixed, severity):
    text = (
        f"Package: {pkg}\nInstalled Version: {installed}\nVulnerability {cve}\nSeverity: {severity}\n"
        f"Fixed Version: {fixed}\nLink: [{cve}](https://avd.aquasec.com/nvd/{cve.lower()})"
    )
    return result(cve, "package-lock.json", 1, text=text)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def full_reports(reports: Path) -> None:
    team = reports / "equipo-01"
    write_json(team / "scc.json", {
        "totals": {"languages": 2, "files": 12, "code": 900},
        "languages": [
            {"name": "TypeScript", "files": 10, "code": 800, "comment": 5, "complexity": 30},
            {"name": "CSS", "files": 2, "code": 100, "comment": 0, "complexity": 0},
        ],
    })
    write_json(team / "megalinter.sarif", {"runs": [
        run("ESLint", [result("no-unused-vars", "file:///tmp/lint/src/app.ts", 3),
                       result("eqeqeq", "/tmp/lint/src/app.ts", 9, level="warning")]),
        run("Prettier", []),
        run("Betterleaks", [result("generic-api-key", "/tmp/lint/.env", 2, text=f"secret {SECRET}")]),
        run("Trivy", [trivy_result("CVE-2021-1", "lodash", "4.17.15", "4.17.21", "HIGH")]),
    ]})
    write_json(team / "trivy.sarif", {"runs": [run("Trivy", [
        trivy_result("CVE-2021-1", "lodash", "4.17.15", "4.17.21", "HIGH"),
        trivy_result("CVE-2021-2", "minimist", "1.0.0", "", "CRITICAL"),
        trivy_result("CVE-2021-3", "axios", "0.1.0", "0.2.0", "LOW"),
    ], rules=[{"id": "CVE-2021-1", "properties": {"security-severity": "7.5"}}])]})
    write_json(team / "semgrep.sarif", {"runs": [run("Semgrep OSS", [
        result("javascript.sqli", "src/db.ts", 14),
    ], rules=[{"id": "javascript.sqli", "properties": {"tags": [
        "CWE-89: Improper Neutralization of Special Elements used in an SQL Command",
        "OWASP-A03:2021 - Injection",
    ]}}])]})
    write_json(team / "scanoss.json", {
        "totals": {"files_scanned": 10, "files_matched": 1, "matched_percent": 10.0,
                   "file_matches": 1, "snippet_matches": 0},
        "matches": [{"path": "src/util.ts", "match": "file", "matched_percent": 100.0,
                     "component": "lodash", "vendor": "lodash", "version": "4.17.21",
                     "licenses": ["MIT"], "url": "https://github.com/lodash/lodash"}],
    })
    write_json(team / "git-signals.json", {
        "has_git": True, "commit_count": 1, "single_commit": True,
        "first_commit": {"author_date": "2026-10-20T10:00:00+00:00"},
        "outside_event": {"before_start": 1, "after_end": 0, "commits": []},
    })
    dolos = reports / "dolos" / "pairs.csv"
    dolos.parent.mkdir(parents=True, exist_ok=True)
    dolos.write_text(
        "team_a,team_b,similarity,file_pairs,longest_fragment,total_overlap,file_a,file_b\n"
        "equipo-01,equipo-02,0.85,3,40,120,src/app.ts,index.ts\n"
        "equipo-01,equipo-03,0,0,0,0,,\n"
        "equipo-02,equipo-03,0.5,1,10,20,a.py,b.py\n",
        encoding="utf-8",
    )


def test_report_follows_spec_template_with_all_results(tmp_path):
    full_reports(tmp_path)
    text = build_report(TEAM, EVENT, tmp_path, names={"equipo-02": "Equipo Beta"}, now=NOW)
    positions = [text.index(heading) for heading in TEMPLATE_HEADINGS]
    assert positions == sorted(positions)
    assert "TypeScript, CSS; 900 líneas de código en 12 archivo(s)" in text
    assert "[http://localhost:3000](<http://localhost:3000>)" in text
    assert "Generado: 2026-11-05T12:00:00+00:00" in text
    # Security: only HIGH/CRITICAL with a fix count; secrets counted; Semgrep classified.
    assert "CVE críticas/altas con corrección disponible: **1** (total de CVE en dependencias: 3" in text
    assert "| CVE-2021-1 | lodash | 4.17.15 | 4.17.21 | 7.5 | alta |" in text
    assert "CWE-89 (1)" in text and "A03:2021 - Injection (1)" in text
    assert "Secretos expuestos: **1**" in text
    assert "| generic-api-key | .env:2 |" in text
    assert "Hallazgos críticos: 1 CVE crítica(s)/alta(s) con corrección disponible; 1 secreto(s)" in text
    # Quality excludes security linters.
    assert "| ESLint | 1 | 1 |" in text and "| Prettier | 0 | 0 |" in text
    assert "Trivy |" not in text.split("## Calidad del código")[1].split("## Seguridad")[0]
    assert "src/app.ts" in text
    # Plagiarism.
    assert "1 de 10 archivo(s) con coincidencias (10.0%)" in text
    assert "[lodash lodash 4.17.21](<https://github.com/lodash/lodash>)" in text
    assert "similitud máxima 85% con Equipo Beta; 1 de 2 equipo(s) con archivos similares" in text
    assert "**un solo commit con todo el código**" in text
    assert "1 antes del inicio" in text
    # Recommendations from the findings.
    assert "Revocar y rotar los secretos" in text
    assert "Actualizar las dependencias con CVE críticas/altas que ya tienen corrección: lodash." in text


def test_report_never_contains_secret_values(tmp_path):
    full_reports(tmp_path)
    assert SECRET not in build_report(TEAM, EVENT, tmp_path, now=NOW)


def test_report_without_results_says_what_is_missing(tmp_path):
    text = build_report(TEAM, EVENT, tmp_path, now=NOW)
    for heading in TEMPLATE_HEADINGS:
        assert heading in text
    assert "Sin datos: falta `scc.json`; corre `acr scc equipo-01`." in text
    assert "Sin datos: falta `trivy.sarif`; corre `acr megalinter equipo-01`." in text
    assert "Sin datos: falta `scanoss.json`; corre `acr scanoss equipo-01`." in text
    assert "Sin datos: falta `dolos/pairs.csv`; corre `acr dolos`." in text
    assert "Sin datos: falta `git-signals.json`; corre `acr git-signals equipo-01`." in text
    assert "Pendiente: revisión asistida con Claude Code (`review.json`, PB-13)." in text
    assert "Pendiente: búsqueda web con Claude Code" in text
    assert "ninguno en los resultados disponibles (faltan" in text
    assert "Sin recomendaciones automáticas" in text


def test_report_handles_unreadable_results(tmp_path):
    team = tmp_path / "equipo-01"
    team.mkdir()
    (team / "scc.json").write_text("{not json", encoding="utf-8")
    (team / "megalinter.sarif").write_text("[]", encoding="utf-8")
    text = build_report(TEAM, EVENT, tmp_path, now=NOW)
    assert "no se pudo leer `scc.json`" in text
    assert "no se pudo leer `megalinter.sarif`" in text


def test_report_mentions_assisted_review_files_when_present(tmp_path):
    write_json(tmp_path / "equipo-01" / "review.json", {})
    write_json(tmp_path / "equipo-01" / "web-plagiarism.json", {})
    text = build_report(TEAM, EVENT, tmp_path, now=NOW)
    assert "Ver `review.json`" in text and "ver `web-plagiarism.json`" in text


def test_report_without_git_history_and_single_team_in_dolos(tmp_path):
    write_json(tmp_path / "equipo-01" / "git-signals.json", {"has_git": False})
    (tmp_path / "dolos").mkdir()
    (tmp_path / "dolos" / "pairs.csv").write_text("team_a,team_b,similarity\n", encoding="utf-8")
    text = build_report(TEAM, EVENT, tmp_path, now=NOW)
    assert "no trae historial de git" in text
    assert "no se comparó con Dolos" in text


def test_untrusted_text_is_escaped(tmp_path):
    team = Team("equipo-01", "<script>alert(1)</script>", "A | B", "o", "r")
    write_json(tmp_path / "equipo-01" / "scanoss.json", {
        "totals": {}, "matches": [{"path": "x|<img src=x>.js", "url": "javascript:alert(1)",
                                   "component": "c"}],
    })
    text = build_report(team, EVENT, tmp_path, now=NOW)
    assert "<script>" not in text and "<img" not in text
    assert "&lt;script&gt;" in text and "A \\| B" in text
    assert "](<javascript" not in text


def test_md_and_link_helpers():
    assert md("a|b\nc") == "a\\|b c"
    assert md("[x](y)") == "\\[x\\]\\(y\\)"
    assert link("https://e.org/x", "e") == "[e](<https://e.org/x>)"
    assert link("file:///etc/passwd") == "file:///etc/passwd"
    assert link(None) == "—"


def test_sarif_and_trivy_parsing():
    sarif = {"runs": [run("Trivy", [
        trivy_result("CVE-1", "a", "1", "2", "CRITICAL"),
        trivy_result("CVE-1", "a", "1", "2", "CRITICAL"),  # same CVE in another lockfile
        result("CVE-9", "x", None, text="no fields"),
    ], rules=[{"id": "CVE-9", "properties": {"security-severity": "9.1"}}]), {"bad": 1}]}
    findings = sarif_findings(sarif)
    assert len(findings) == 3 and findings[2].line is None
    vulns = vulnerabilities(sarif)
    assert [(v.cve, v.severity) for v in vulns] == [("CVE-9", "CRITICAL"), ("CVE-1", "CRITICAL")]
    assert [v.cve for v in fixable_critical(vulns)] == ["CVE-1"]


def test_empty_fixed_version_is_not_fixable():
    sarif = {"runs": [run("Trivy", [trivy_result("CVE-2", "m", "1", "", "CRITICAL")])]}
    [vuln] = vulnerabilities(sarif)
    assert vuln.fixed == "" and fixable_critical([vuln]) == []


def test_write_report_and_invalid_id(tmp_path):
    full_reports(tmp_path)
    path = write_report(TEAM, EVENT, tmp_path, now=NOW)
    assert path == tmp_path / "equipo-01" / REPORT_FILE
    assert path.read_text(encoding="utf-8").startswith("# Reporte de revisión – Equipo Alfa")
    assert not (tmp_path / "equipo-01" / f".{REPORT_FILE}.tmp").exists()
    with pytest.raises(ReportError):
        build_report(Team("../x", "n", "p", "o", "r"), EVENT, tmp_path)


# --- CLI -----------------------------------------------------------------------------------


def write_config(tmp_path: Path, ids: list[str]) -> Path:
    data = {
        "evento": {"nombre": "H", "inicio": "2026-11-01T09:00:00-06:00", "fin": "2026-11-03T18:00:00-06:00"},
        "equipos": [
            {"id": i, "nombre": f"N {i}", "proyecto": "P", "objetivo": "O", "repo": "https://example.org/r.git"}
            for i in ids
        ],
    }
    path = tmp_path / "equipos.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_cli_report_all_and_one(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    full_reports(tmp_path / "reports")
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "report", "--dashboard-url", "http://localhost:3001"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "equipo-01: reporte -> reports/equipo-01/reporte.md" in out
    text = (tmp_path / "reports" / "equipo-01" / REPORT_FILE).read_text(encoding="utf-8")
    assert "http://localhost:3001" in text and "con N equipo-02" in text
    assert (tmp_path / "reports" / "equipo-02" / REPORT_FILE).is_file()
    assert main(["--config", str(config), "report", "equipo-02"]) == EXIT_OK


def test_cli_report_unknown_team_and_bad_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = write_config(tmp_path, ["equipo-01"])
    assert main(["--config", str(config), "report", "otro"]) == EXIT_ERROR
    assert main(["--config", str(tmp_path / "nope.yml"), "report"]) == EXIT_ERROR
