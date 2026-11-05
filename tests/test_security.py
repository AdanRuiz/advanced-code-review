"""Seguridad page (PB-21, SPEC.md "Dashboard local" > "4. Seguridad").

Covers ``reports/security.json`` (built from SARIF files in a temporary folder) and the Grafana
dashboard that reads it, as data: no Grafana, container engine, MegaLinter or network.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.security import SECURITY_FILE, SecurityError, build_security, team_security, write_security

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "services" / "grafana" / "dashboards" / "seguridad.json"
SECURITY_URL = "http://files:8080/reports/security.json"
SECRET = "ghp_" + "A" * 36  # fake token: must never reach security.json
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


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


def team_reports(reports: Path, team_id: str = "equipo-01") -> None:
    team = reports / team_id
    write_json(team / "trivy.sarif", {"runs": [run("Trivy", [
        trivy_result("CVE-2021-1", "lodash", "4.17.15", "4.17.21", "HIGH"),
        trivy_result("CVE-2021-2", "minimist", "1.0.0", "", "CRITICAL"),  # no fix: not in indicator
        trivy_result("CVE-2021-3", "axios", "0.1.0", "0.2.0", "LOW"),  # fix, but low
        trivy_result("CVE-2021-4", "express", "4.0.0", "4.19.2", "CRITICAL"),
        trivy_result("CVE-2021-4", "express", "4.0.0", "4.19.2", "CRITICAL"),  # other lockfile
        trivy_result("CVE-2021-5", "qs", "6.0.0", "6.2.0", "MEDIUM"),
    ], rules=[
        {"id": "CVE-2021-1", "properties": {"security-severity": "7.5"}},
        {"id": "CVE-2021-4", "properties": {"security-severity": "9.8"}},
    ])]})
    write_json(team / "semgrep.sarif", {"runs": [run("Semgrep OSS", [
        result("javascript.sqli", "src/db.ts", 14),
        result("javascript.sqli", "src/users.ts", 3),
        result("javascript.xss", "src/view.ts", 8, level="warning"),
        result("generic.todo", "src/a.ts", 1, level="note"),
    ], rules=[
        {"id": "javascript.sqli", "properties": {"tags": [
            "CWE-89: Improper Neutralization of Special Elements used in an SQL Command",
            "OWASP-A03:2021 - Injection",
        ]}},
        {"id": "javascript.xss", "properties": {"tags": [
            "CWE-79: Improper Neutralization of Input During Web Page Generation",
            "OWASP-A03:2021 - Injection",
        ]}},
    ])]})
    write_json(team / "megalinter.sarif", {"runs": [
        run("ESLint", [result("no-unused-vars", "/tmp/lint/src/app.ts", 3)]),
        run("Betterleaks", [result("github-pat", "/tmp/lint/.env", 2, text=f"token {SECRET}")]),
    ]})


# --- security.json ----------------------------------------------------------------------------


def test_cve_by_severity_and_main_indicator(tmp_path):
    team_reports(tmp_path)
    data = team_security("equipo-01", tmp_path)
    [summary] = data["teams"]
    assert summary["trivy"] is True
    assert summary["cve_total"] == 5  # duplicated CVE in another lockfile counted once
    assert (summary["cve_critical"], summary["cve_high"], summary["cve_medium"], summary["cve_low"]) == (
        2, 1, 1, 1,
    )
    # Only critical/high with a fixed version: CVE-2021-4 and CVE-2021-1.
    assert summary["cve_fixable_critical_high"] == 2
    vulns = data["vulnerabilities"]
    assert [v["cve"] for v in vulns][:2] == ["CVE-2021-4", "CVE-2021-1"]  # highest CVSS first
    assert {v["cve"] for v in vulns if v["fixable"]} == {"CVE-2021-4", "CVE-2021-1"}
    lodash = next(v for v in vulns if v["cve"] == "CVE-2021-1")
    assert lodash == {
        "team": "equipo-01", "cve": "CVE-2021-1", "package": "lodash", "installed": "4.17.15",
        "fixed": "4.17.21", "cvss": 7.5, "severity": "HIGH", "fixable": True,
    }


def test_semgrep_by_cwe_and_owasp(tmp_path):
    team_reports(tmp_path)
    data = team_security("equipo-01", tmp_path)
    [summary] = data["teams"]
    assert (summary["semgrep"], summary["semgrep_findings"], summary["semgrep_errors"]) == (True, 4, 2)
    counts = {(w["kind"], w["category"]): w["count"] for w in data["weaknesses"]}
    assert counts == {
        ("CWE", "CWE-89"): 2,
        ("CWE", "CWE-79"): 1,
        ("CWE", "sin clasificar"): 1,
        ("OWASP", "A03:2021 - Injection"): 3,
        ("OWASP", "sin clasificar"): 1,
    }
    patterns = data["insecure_patterns"]
    assert patterns[0] == {
        "team": "equipo-01", "rule": "javascript.sqli", "cwe": "CWE-89",
        "owasp": "A03:2021 - Injection", "severity": "error", "file": "src/db.ts", "line": 14,
    }
    # Errors first, then by file and line.
    assert [(p["severity"], p["file"]) for p in patterns] == [
        ("error", "src/db.ts"), ("error", "src/users.ts"), ("note", "src/a.ts"), ("warning", "src/view.ts"),
    ]


def test_secrets_only_type_file_and_line(tmp_path):
    team_reports(tmp_path)
    data = team_security("equipo-01", tmp_path)
    assert data["teams"][0]["secrets"] == 1
    assert data["secrets"] == [{"team": "equipo-01", "type": "github-pat", "file": ".env", "line": 2}]
    path, _ = write_security(["equipo-01"], tmp_path, now=NOW)
    assert SECRET not in path.read_text(encoding="utf-8")


def test_missing_or_unreadable_sarif_gives_empty_team(tmp_path):
    (tmp_path / "equipo-02").mkdir()
    (tmp_path / "equipo-02" / "trivy.sarif").write_text("{not json", encoding="utf-8")
    data = team_security("equipo-02", tmp_path)
    [summary] = data["teams"]
    assert (summary["trivy"], summary["semgrep"], summary["secrets_scanned"]) == (False, False, False)
    assert summary["cve_total"] == summary["cve_fixable_critical_high"] == 0
    assert data["vulnerabilities"] == data["weaknesses"] == data["secrets"] == []


@pytest.mark.parametrize("team_id", ["../x", "A", ""])
def test_rejects_invalid_team_id(tmp_path, team_id):
    with pytest.raises(SecurityError):
        team_security(team_id, tmp_path)


def test_build_and_write_all_teams(tmp_path):
    team_reports(tmp_path, "equipo-01")
    data = build_security(["equipo-01", "equipo-02"], tmp_path, now=NOW)
    assert data["schema_version"] == 1 and data["generated_at"] == "2026-11-05T12:00:00+00:00"
    assert [t["team"] for t in data["teams"]] == ["equipo-01", "equipo-02"]
    path, written = write_security(["equipo-01", "equipo-02"], tmp_path, now=NOW)
    assert path == tmp_path / SECURITY_FILE
    assert json.loads(path.read_text(encoding="utf-8")) == written == data
    assert not list(tmp_path.glob(".*.tmp"))


# --- CLI --------------------------------------------------------------------------------------


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


def test_cli_security(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    team_reports(tmp_path / "reports", "equipo-01")
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "security"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "2 CVE crítica(s)/alta(s) con corrección, 5 CVE en total" in captured.out
    assert "4 hallazgo(s) de Semgrep y 1 secreto(s) en 2 equipo(s) -> reports/security.json" in captured.out
    assert "sin SARIF de Trivy, Semgrep o MegaLinter para equipo-02" in captured.err
    assert SECRET not in captured.out + captured.err
    data = json.loads((tmp_path / "reports" / SECURITY_FILE).read_text(encoding="utf-8"))
    assert [t["team"] for t in data["teams"]] == ["equipo-01", "equipo-02"]


def test_cli_security_invalid_config(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nope.yml"), "security"]) == EXIT_ERROR


# --- dashboard --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def data_panels(dashboard: dict) -> dict[str, dict]:
    return {panel["title"]: panel for panel in dashboard["panels"] if panel.get("targets")}


def query(panel: dict) -> dict:
    (target,) = panel["targets"]
    return target


def test_panels_of_the_spec(dashboard):
    panels = {title: panel["type"] for title, panel in data_panels(dashboard).items()}
    assert panels == {
        "CVE críticas/altas con corrección": "stat",
        "Hallazgos de Semgrep": "stat",
        "Secretos (Betterleaks)": "stat",
        "Total de CVE": "stat",
        "CVE por severidad": "barchart",
        "Dependencias vulnerables": "table",
        "Código inseguro por CWE": "barchart",
        "Código inseguro por OWASP Top 10": "barchart",
        "Patrones inseguros": "table",
        "Secretos expuestos": "table",
    }
    # The main indicator comes first, top left.
    first = min(dashboard["panels"][1:], key=lambda p: (p["gridPos"]["y"], p["gridPos"]["x"]))
    assert first["title"] == "CVE críticas/altas con corrección"


def test_every_panel_reads_security_json_filtered_by_equipo(dashboard, tmp_path):
    team_reports(tmp_path)
    data = build_security(["equipo-01"], tmp_path, now=NOW)
    for title, panel in data_panels(dashboard).items():
        target = query(panel)
        assert target["datasource"]["uid"] == "acr-infinity", title
        assert target["url"] == SECURITY_URL, title
        assert target["parser"] == "backend", title
        assert target["filterExpression"].startswith("team IN (${equipo:singlequote})"), title
        # Every selector exists in the rows that acr security writes.
        rows = data[target["root_selector"]]
        assert rows, title
        for column in target["columns"]:
            assert column["selector"] in rows[0], (title, column["selector"])


def test_cve_by_severity_is_stacked_per_team(dashboard):
    panel = data_panels(dashboard)["CVE por severidad"]
    selectors = [c["selector"] for c in query(panel)["columns"]]
    assert selectors == ["team", "cve_critical", "cve_high", "cve_medium", "cve_low"]
    assert panel["options"]["stacking"] == "normal"
    assert panel["options"]["xField"] == "Equipo"


def test_main_indicator_counts_only_fixable_critical_and_high(dashboard):
    panel = data_panels(dashboard)["CVE críticas/altas con corrección"]
    columns = {c["selector"]: c["text"] for c in query(panel)["columns"]}
    assert "cve_fixable_critical_high" in columns
    field = panel["options"]["reduceOptions"]["fields"]
    assert re.search(field.strip("/"), columns["cve_fixable_critical_high"])
    assert panel["options"]["reduceOptions"]["calcs"] == ["sum"]


def test_cards_are_red_or_green(dashboard):
    for title, panel in data_panels(dashboard).items():
        if panel["type"] != "stat":
            continue
        steps = panel["fieldConfig"]["defaults"]["thresholds"]["steps"]
        assert [(s["color"], s["value"]) for s in steps] == [("green", None), ("red", 1)], title
        assert panel["options"]["colorMode"] == "background", title


def test_weaknesses_grouped_by_cwe_and_owasp(dashboard):
    panels = data_panels(dashboard)
    for title, kind in (("Código inseguro por CWE", "CWE"), ("Código inseguro por OWASP Top 10", "OWASP")):
        panel = panels[title]
        assert query(panel)["filterExpression"].endswith(f"&& kind == '{kind}'")
        (transformation,) = panel["transformations"]
        assert transformation["id"] == "groupingToMatrix"
        assert transformation["options"]["rowField"] == "team"
        assert transformation["options"]["columnField"] == "category"


def test_vulnerable_dependencies_sorted_by_cvss(dashboard):
    panel = data_panels(dashboard)["Dependencias vulnerables"]
    texts = [c["text"] for c in query(panel)["columns"]]
    for column in ("CVE", "Paquete", "Versión instalada", "Versión corregida", "CVSS", "Severidad"):
        assert column in texts
    assert panel["options"]["sortBy"] == [{"displayName": "CVSS", "desc": True}]


def test_secrets_table_never_shows_a_value(dashboard):
    panel = data_panels(dashboard)["Secretos expuestos"]
    assert [c["selector"] for c in query(panel)["columns"]] == ["team", "type", "file", "line"]


def test_team_cells_open_the_equipo_page(dashboard):
    for title, panel in data_panels(dashboard).items():
        if panel["type"] == "stat" or "transformations" in panel:
            continue
        links = [
            link
            for override in panel["fieldConfig"]["overrides"]
            if override["matcher"] == {"id": "byName", "options": "Equipo"}
            for prop in override["properties"]
            if prop["id"] == "links"
            for link in prop["value"]
        ]
        assert [link["url"] for link in links] == ["/d/acr-equipo?var-equipo=${__value.raw}"], title
