"""Security data of every team for the Seguridad page (PB-21, SPEC.md "Dashboard local" > "4. Seguridad").

MegaLinter does not send the severity as a metric, so the page reads the SARIF of Trivy and
Semgrep (SPEC.md, "Uso de CVE y CWE"). Infinity reads one URL per query and cannot loop over the
teams picked in the ``equipo`` variable, so this module flattens the SARIF of every team into one
file, ``reports/security.json``, and the panels filter its rows by team:

- ``teams``: per team, CVE by CVSS severity, **critical/high CVE with a fixed version** (the main
  indicator), Semgrep findings and secrets, plus which SARIF files were available.
- ``vulnerabilities``: one row per CVE, package and installed version (``trivy.sarif``).
- ``weaknesses``: Semgrep findings counted by CWE and by OWASP Top 10 category (``semgrep.sarif``).
- ``insecure_patterns``: rule, CWE, OWASP, severity, file and line (``semgrep.sarif``).
- ``secrets``: Betterleaks findings from ``megalinter.sarif`` with **only** type, file and line;
  the value is never read into the output (AGENTS.md, invariant 6).

The parsing of SARIF is shared with the Markdown report (``acr.report``), so both say the same.
Nothing is executed and nothing leaves the machine: it only reads files in ``reports/``.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acr.config import is_valid_team_id
from acr.report import (
    _SECRET_TOOLS,
    _SEVERITIES,
    _is_tool,
    fixable_critical,
    sarif_findings,
    vulnerabilities,
    weakness_tags,
)

DEFAULT_REPORTS_DIR = Path("reports")
SECURITY_FILE = "security.json"
SCHEMA_VERSION = 1
UNCLASSIFIED = "sin clasificar"

TEAM_COLUMNS = (
    "team",
    "trivy",
    "semgrep",
    "secrets_scanned",
    "cve_total",
    "cve_critical",
    "cve_high",
    "cve_medium",
    "cve_low",
    "cve_unknown",
    "cve_fixable_critical_high",
    "semgrep_findings",
    "semgrep_errors",
    "secrets",
)


class SecurityError(Exception):
    """The security summary could not be written; shown to the reviewer."""


def _read_sarif(path: Path) -> dict[str, Any] | None:
    """The SARIF at ``path``, or ``None`` if it is missing or unreadable."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _empty_team(team_id: str) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(TEAM_COLUMNS, 0)
    row.update(team=team_id, trivy=False, semgrep=False, secrets_scanned=False)
    return row


def _severity(level: str) -> str:
    return level if level in ("error", "warning", "note") else "warning"


def team_security(team_id: str, reports_dir: Path = DEFAULT_REPORTS_DIR) -> dict[str, list[dict[str, Any]]]:
    """Rows of every section of ``security.json`` for one team."""
    if not is_valid_team_id(team_id):
        raise SecurityError(f"id inválido ({team_id!r})")
    team_dir = reports_dir / team_id
    summary = _empty_team(team_id)
    rows: dict[str, list[dict[str, Any]]] = {
        "teams": [summary],
        "vulnerabilities": [],
        "weaknesses": [],
        "insecure_patterns": [],
        "secrets": [],
    }

    # Dependencies (Trivy): CVE by CVSS severity and the main indicator.
    trivy = _read_sarif(team_dir / "trivy.sarif")
    if trivy is not None:
        vulns = vulnerabilities(trivy)
        by_severity = Counter(v.severity for v in vulns)
        summary["trivy"] = True
        summary["cve_total"] = len(vulns)
        for severity in _SEVERITIES:
            summary[f"cve_{severity.lower()}"] = by_severity[severity]
        fixable = set(fixable_critical(vulns))
        summary["cve_fixable_critical_high"] = len(fixable)
        rows["vulnerabilities"] = [
            {
                "team": team_id,
                "cve": v.cve,
                "package": v.package,
                "installed": v.installed,
                "fixed": v.fixed,
                "cvss": v.cvss,
                "severity": v.severity,
                "fixable": v in fixable,
            }
            for v in vulns
        ]

    # Insecure code (Semgrep): CWE and OWASP Top 10.
    semgrep = _read_sarif(team_dir / "semgrep.sarif")
    if semgrep is not None:
        findings = sarif_findings(semgrep)
        summary["semgrep"] = True
        summary["semgrep_findings"] = len(findings)
        summary["semgrep_errors"] = sum(1 for f in findings if f.level == "error")
        cwe_count: Counter[str] = Counter()
        owasp_count: Counter[str] = Counter()
        patterns = []
        for finding in sorted(findings, key=lambda f: (f.level != "error", f.path, f.line or 0)):
            cwe, owasp = weakness_tags(finding)
            cwe_count.update(cwe or [UNCLASSIFIED])
            owasp_count.update(owasp or [UNCLASSIFIED])
            patterns.append(
                {
                    "team": team_id,
                    "rule": finding.rule,
                    "cwe": ", ".join(cwe),
                    "owasp": ", ".join(owasp),
                    "severity": _severity(finding.level),
                    "file": finding.path,
                    "line": finding.line,
                }
            )
        rows["insecure_patterns"] = patterns
        rows["weaknesses"] = [
            {"team": team_id, "kind": kind, "category": category, "count": count}
            for kind, counts in (("CWE", cwe_count), ("OWASP", owasp_count))
            for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    # Secrets (Betterleaks): type, file and line only, never the value or the message.
    megalinter = _read_sarif(team_dir / "megalinter.sarif")
    if megalinter is not None:
        secrets = [f for f in sarif_findings(megalinter) if _is_tool(f.tool, _SECRET_TOOLS)]
        summary["secrets_scanned"] = True
        summary["secrets"] = len(secrets)
        rows["secrets"] = [
            {"team": team_id, "type": s.rule, "file": s.path, "line": s.line} for s in secrets
        ]
    return rows


def build_security(
    team_ids: Iterable[str],
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The content of ``reports/security.json`` for ``team_ids`` (in that order)."""
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (now or datetime.now(UTC).astimezone()).isoformat(timespec="seconds"),
        "teams": [],
        "vulnerabilities": [],
        "weaknesses": [],
        "insecure_patterns": [],
        "secrets": [],
    }
    for team_id in team_ids:
        for section, rows in team_security(team_id, reports_dir).items():
            data[section].extend(rows)
    return data


def write_security(
    team_ids: Iterable[str],
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    *,
    now: datetime | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Write ``reports/security.json`` atomically and return its path and content."""
    data = build_security(team_ids, reports_dir, now=now)
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / SECURITY_FILE
    staging = reports_dir / f".{SECURITY_FILE}.tmp"
    staging.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staging.replace(target)
    return target, data
