"""Markdown report per team (PB-14, SPEC.md "Entregables").

Builds ``reports/<id>/reporte.md`` with the template of SPEC.md "Entregables" from every result
available in ``reports/``:

- ``scc.json`` (PB-8): languages and size.
- ``megalinter.sarif`` (PB-9): findings per quality linter; Betterleaks run for secrets.
- ``trivy.sarif`` and ``semgrep.sarif`` (PB-9): CVE of dependencies and insecure code.
- ``scanoss.json`` (PB-10), ``dolos/pairs.csv`` (PB-11) and ``git-signals.json`` (PB-7).
- ``review.json`` and ``web-plagiarism.json`` (assisted review): their format arrives with PB-13,
  so for now the report only says whether they exist.

A missing or unreadable result never stops the report: its section says which file is missing
and which command creates it. Detected secrets are never written: only their rule, file and
line (AGENTS.md, invariant 6). Text from the team's repository (paths, rule ids, URLs) is
escaped so it cannot inject Markdown or HTML into the report or its PDF.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from acr.config import Event, Team, is_valid_team_id

DEFAULT_REPORTS_DIR = Path("reports")
DEFAULT_DASHBOARD_URL = "http://localhost:3000"
REPORT_FILE = "reporte.md"
MAX_ROWS = 20

# MegaLinter runs the linters on the repository mounted here (acr.megalinter.WORKSPACE).
_WORKSPACE_PREFIXES = ("/tmp/lint/", "tmp/lint/")
_SECRET_TOOLS = ("betterleaks", "gitleaks")
_SECURITY_TOOLS = (*_SECRET_TOOLS, "trivy", "semgrep")
_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
_SEVERITY_ES = {
    "CRITICAL": "crítica",
    "HIGH": "alta",
    "MEDIUM": "media",
    "LOW": "baja",
    "UNKNOWN": "desconocida",
}
_CWE_RE = re.compile(r"\bCWE-\d+\b")
_OWASP_RE = re.compile(r"\bA\d{2}:\d{4}\b(?:\s*-\s*[^,;\]]+)?")
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|~])")


class ReportError(Exception):
    """The report could not be written; shown to the reviewer."""


# --------------------------------------------------------------------------- Markdown helpers


def md(value: Any) -> str:
    """Escape untrusted text for a Markdown paragraph or table cell (one line, no HTML)."""
    text = " ".join(str(value).split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MD_SPECIAL.sub(r"\\\1", text)


def link(url: Any, label: Any | None = None) -> str:
    """A link only for plain http(s) URLs; anything else is shown as escaped text."""
    if isinstance(url, str) and re.fullmatch(r"https?://[^\s<>()\[\]\"'`]+", url):
        return f"[{md(label if label is not None else url)}](<{url}>)"
    return md(url) if url else "—"


def table(headers: Iterable[str], rows: Iterable[Iterable[str]]) -> list[str]:
    headers = list(headers)
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def percent(value: float) -> str:
    return f"{value:.0%}"


# --------------------------------------------------------------------------- loading results


@dataclass
class Sources:
    """Results read from ``reports/``; ``missing`` and ``unreadable`` list file names."""

    data: dict[str, Any] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)
    unreadable: set[str] = field(default_factory=set)

    def note(self, name: str, command: str) -> str:
        """Why a section has no data, and how to get it."""
        if name in self.unreadable:
            return f"Sin datos: no se pudo leer `{name}`; vuelve a correr `{command}`."
        return f"Sin datos: falta `{name}`; corre `{command}`."


def _read_json(path: Path, sources: Sources, key: str) -> None:
    if not path.is_file():
        sources.missing.add(key)
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        sources.unreadable.add(key)
        return
    if not isinstance(data, dict):
        sources.unreadable.add(key)
        return
    sources.data[key] = data


def _read_pairs(path: Path, sources: Sources, key: str) -> None:
    if not path.is_file():
        sources.missing.add(key)
        return
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            sources.data[key] = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error):
        sources.unreadable.add(key)


def load_sources(team_id: str, reports_dir: Path) -> Sources:
    sources = Sources()
    team_dir = reports_dir / team_id
    for name in (
        "scc.json",
        "megalinter.sarif",
        "trivy.sarif",
        "semgrep.sarif",
        "scanoss.json",
        "git-signals.json",
    ):
        _read_json(team_dir / name, sources, name)
    _read_pairs(reports_dir / "dolos" / "pairs.csv", sources, "dolos/pairs.csv")
    for name in ("review.json", "web-plagiarism.json"):
        if (team_dir / name).is_file():
            sources.data[name] = True
        else:
            sources.missing.add(name)
    return sources


# --------------------------------------------------------------------------- SARIF


@dataclass(frozen=True)
class Finding:
    tool: str
    rule: str
    level: str
    path: str
    line: int | None
    message: str
    rule_props: dict[str, Any]

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_path(uri: Any) -> str:
    text = str(uri or "").strip()
    if text.startswith("file://"):
        text = text[len("file://") :]
    for prefix in _WORKSPACE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text or "—"


def sarif_findings(sarif: dict[str, Any]) -> list[Finding]:
    """Every result of every run, with its tool name and the properties of its rule."""
    findings = []
    for run in _list(sarif.get("runs")):
        run = _dict(run)
        driver = _dict(_dict(run.get("tool")).get("driver"))
        tool = str(driver.get("name") or "desconocido")
        rules = {
            str(rule.get("id")): _dict(rule.get("properties"))
            for rule in map(_dict, _list(driver.get("rules")))
            if rule.get("id") is not None
        }
        for result in map(_dict, _list(run.get("results"))):
            rule = str(result.get("ruleId") or "—")
            locations = _list(result.get("locations"))
            location = _dict(_dict(locations[0]).get("physicalLocation")) if locations else {}
            line = _dict(location.get("region")).get("startLine")
            findings.append(
                Finding(
                    tool=tool,
                    rule=rule,
                    level=str(result.get("level") or "warning"),
                    path=_clean_path(_dict(location.get("artifactLocation")).get("uri")),
                    line=line if isinstance(line, int) and not isinstance(line, bool) else None,
                    message=str(_dict(result.get("message")).get("text") or ""),
                    rule_props=rules.get(rule, {}),
                )
            )
    return findings


def sarif_tools(sarif: dict[str, Any]) -> list[str]:
    names = []
    for run in map(_dict, _list(sarif.get("runs"))):
        names.append(str(_dict(_dict(run.get("tool")).get("driver")).get("name") or "desconocido"))
    return names


def _is_tool(name: str, tools: Iterable[str]) -> bool:
    lowered = name.lower()
    return any(tool in lowered for tool in tools)


# --------------------------------------------------------------------------- Trivy and Semgrep


@dataclass(frozen=True)
class Vulnerability:
    cve: str
    package: str
    installed: str
    fixed: str
    severity: str
    cvss: float | None


def _field(message: str, name: str) -> str:
    # [ \t] and not \s: an empty "Fixed Version:" must not take the next line as its value.
    match = re.search(rf"^[ \t]*{re.escape(name)}:[ \t]*(.*?)[ \t\r]*$", message, re.MULTILINE)
    return match.group(1) if match else ""


def _cvss(props: dict[str, Any]) -> float | None:
    try:
        return float(props.get("security-severity"))
    except (TypeError, ValueError):
        return None


def _severity(finding: Finding, cvss: float | None) -> str:
    severity = _field(finding.message, "Severity").upper()
    if severity in _SEVERITIES:
        return severity
    for tag in _list(finding.rule_props.get("tags")):
        if str(tag).upper() in _SEVERITIES:
            return str(tag).upper()
    if cvss is None:
        return "UNKNOWN"
    return "CRITICAL" if cvss >= 9 else "HIGH" if cvss >= 7 else "MEDIUM" if cvss >= 4 else "LOW"


def vulnerabilities(sarif: dict[str, Any]) -> list[Vulnerability]:
    """Trivy results: one per CVE and package, highest CVSS first."""
    found: dict[tuple[str, str, str], Vulnerability] = {}
    for finding in sarif_findings(sarif):
        cvss = _cvss(finding.rule_props)
        vuln = Vulnerability(
            cve=_field(finding.message, "Vulnerability") or finding.rule,
            package=_field(finding.message, "Package") or "—",
            installed=_field(finding.message, "Installed Version") or "—",
            fixed=_field(finding.message, "Fixed Version"),
            severity=_severity(finding, cvss),
            cvss=cvss,
        )
        found.setdefault((vuln.cve, vuln.package, vuln.installed), vuln)
    return sorted(found.values(), key=lambda v: (-(v.cvss or 0), _SEVERITIES.index(v.severity), v.cve))


def fixable_critical(vulns: Iterable[Vulnerability]) -> list[Vulnerability]:
    """The main indicator of SPEC.md: critical/high CVE with a fixed version available."""
    return [v for v in vulns if v.severity in ("CRITICAL", "HIGH") and v.fixed]


def weakness_tags(finding: Finding) -> tuple[list[str], list[str]]:
    tags = " ; ".join(str(tag) for tag in _list(finding.rule_props.get("tags")))
    cwe = sorted(set(_CWE_RE.findall(tags)))
    owasp = sorted({match.strip() for match in _OWASP_RE.findall(tags)})
    return cwe, owasp


# --------------------------------------------------------------------------- sections


def _languages(sources: Sources, team_id: str) -> tuple[str, list[str]]:
    scc = sources.data.get("scc.json")
    if scc is None:
        return sources.note("scc.json", f"acr scc {team_id}"), []
    totals = _dict(scc.get("totals"))
    languages = [_dict(lang) for lang in _list(scc.get("languages"))]
    names = ", ".join(md(lang.get("name")) for lang in languages[:5]) or "ninguno reconocido"
    summary = (
        f"{names}; {totals.get('code', 0)} líneas de código en {totals.get('files', 0)} archivo(s)"
    )
    rows = [
        [md(lang.get("name")), str(lang.get("files", 0)), str(lang.get("code", 0)),
         str(lang.get("comment", 0)), str(lang.get("complexity", 0))]
        for lang in languages
    ]
    lines = table(["Lenguaje", "Archivos", "Código", "Comentarios", "Complejidad"], rows) if rows else []
    return summary, lines


def _secrets(sources: Sources) -> list[Finding] | None:
    sarif = sources.data.get("megalinter.sarif")
    if sarif is None:
        return None
    return [f for f in sarif_findings(sarif) if _is_tool(f.tool, _SECRET_TOOLS)]


def _quality(sources: Sources, team_id: str) -> list[str]:
    sarif = sources.data.get("megalinter.sarif")
    if sarif is None:
        return [sources.note("megalinter.sarif", f"acr megalinter {team_id}")]
    tools = [name for name in sarif_tools(sarif) if not _is_tool(name, _SECURITY_TOOLS)]
    findings = [f for f in sarif_findings(sarif) if not _is_tool(f.tool, _SECURITY_TOOLS)]
    if not tools:
        return ["MegaLinter no corrió linters de calidad para los lenguajes de este repositorio."]
    errors = Counter(f.tool for f in findings if f.level == "error")
    others = Counter(f.tool for f in findings if f.level != "error")
    clean = [name for name in tools if not errors[name] and not others[name]]
    lines = [
        f"- Linters de calidad: {len(tools)}; sin hallazgos: {len(clean)}; "
        f"con errores: {sum(1 for name in tools if errors[name])}.",
        f"- Hallazgos: {sum(errors.values())} error(es) y {sum(others.values())} advertencia(s) o aviso(s).",
        "",
    ]
    ordered = sorted(set(tools), key=lambda name: (-errors[name], -others[name], name.lower()))
    lines += table(
        ["Linter", "Errores", "Advertencias"],
        ([md(name), str(errors[name]), str(others[name])] for name in ordered),
    )
    files = Counter(f.path for f in findings)
    if files:
        lines += ["", "Archivos con más hallazgos:", ""]
        lines += table(["Archivo", "Hallazgos"], ([md(p), str(n)] for p, n in files.most_common(10)))
    lines += ["", "Detalle completo en `megalinter.md` y en la página Calidad del dashboard."]
    return lines


def _security(sources: Sources, team_id: str) -> tuple[list[str], dict[str, Any]]:
    facts: dict[str, Any] = {}
    lines = []
    # Dependencies (Trivy)
    trivy = sources.data.get("trivy.sarif")
    if trivy is None:
        lines.append("- CVE críticas/altas con corrección disponible: " + sources.note(
            "trivy.sarif", f"acr megalinter {team_id}"))
        cve_lines: list[str] = []
    else:
        vulns = vulnerabilities(trivy)
        fixable = fixable_critical(vulns)
        facts["vulns"], facts["fixable"] = vulns, fixable
        by_severity = Counter(v.severity for v in vulns)
        breakdown = ", ".join(
            f"{by_severity[s]} {_SEVERITY_ES[s]}" for s in _SEVERITIES if by_severity[s]
        ) or "ninguna"
        lines.append(
            f"- CVE críticas/altas con corrección disponible: **{len(fixable)}** "
            f"(total de CVE en dependencias: {len(vulns)}; {breakdown})"
        )
        cve_lines = []
        if fixable:
            cve_lines = ["", "CVE críticas/altas con corrección disponible:", ""]
            cve_lines += table(
                ["CVE", "Paquete", "Instalada", "Corregida", "CVSS", "Severidad"],
                (
                    [md(v.cve), md(v.package), md(v.installed), md(v.fixed),
                     f"{v.cvss:.1f}" if v.cvss is not None else "—", _SEVERITY_ES[v.severity]]
                    for v in fixable[:MAX_ROWS]
                ),
            )
            if len(fixable) > MAX_ROWS:
                cve_lines.append(f"\n… y {len(fixable) - MAX_ROWS} más en `trivy.sarif`.")

    # Insecure code (Semgrep)
    semgrep = sources.data.get("semgrep.sarif")
    code_lines: list[str] = []
    if semgrep is None:
        lines.append("- Código inseguro (CWE / OWASP Top 10): " + sources.note(
            "semgrep.sarif", f"acr megalinter {team_id}"))
    else:
        findings = sarif_findings(semgrep)
        facts["semgrep"] = findings
        categories: Counter[str] = Counter()
        for finding in findings:
            cwe, owasp = weakness_tags(finding)
            categories.update(cwe + owasp or ["sin clasificar"])
        top = ", ".join(f"{md(name)} ({count})" for name, count in categories.most_common(5))
        errors = sum(1 for f in findings if f.level == "error")
        lines.append(
            f"- Código inseguro (CWE / OWASP Top 10): **{len(findings)}** hallazgo(s) de Semgrep, "
            f"{errors} de nivel error" + (f"; categorías: {top}" if top else "")
        )
        if findings:
            code_lines = ["", "Patrones inseguros (Semgrep):", ""]
            ranked = sorted(findings, key=lambda f: (f.level != "error", f.path, f.line or 0))
            code_lines += table(
                ["Regla", "CWE", "OWASP", "Nivel", "Ubicación"],
                (
                    [md(f.rule), md(", ".join(weakness_tags(f)[0]) or "—"),
                     md(", ".join(weakness_tags(f)[1]) or "—"), md(f.level), md(f.where)]
                    for f in ranked[:MAX_ROWS]
                ),
            )
            if len(findings) > MAX_ROWS:
                code_lines.append(f"\n… y {len(findings) - MAX_ROWS} más en `semgrep.sarif`.")

    # Secrets (Betterleaks): never the value, only rule, file and line.
    secrets = _secrets(sources)
    secret_lines: list[str] = []
    if secrets is None:
        lines.append("- Secretos expuestos: " + sources.note("megalinter.sarif", f"acr megalinter {team_id}"))
    else:
        facts["secrets"] = secrets
        lines.append(f"- Secretos expuestos: **{len(secrets)}** (Betterleaks, incluye el historial de git)")
        if secrets:
            secret_lines = ["", "Secretos expuestos (sin mostrar el valor):", ""]
            secret_lines += table(
                ["Tipo", "Ubicación"], ([md(s.rule), md(s.where)] for s in secrets[:MAX_ROWS])
            )
            if len(secrets) > MAX_ROWS:
                secret_lines.append(f"\n… y {len(secrets) - MAX_ROWS} más.")
    return lines + cve_lines + code_lines + secret_lines, facts


def _scanoss(sources: Sources, team_id: str) -> tuple[str, list[str]]:
    report = sources.data.get("scanoss.json")
    if report is None:
        return sources.note("scanoss.json", f"acr scanoss {team_id}"), []
    totals = _dict(report.get("totals"))
    summary = (
        f"{totals.get('files_matched', 0)} de {totals.get('files_scanned', 0)} archivo(s) con "
        f"coincidencias ({totals.get('matched_percent', 0)}%): {totals.get('file_matches', 0)} de "
        f"archivo completo y {totals.get('snippet_matches', 0)} de fragmento"
    )
    matches = [_dict(m) for m in _list(report.get("matches"))]
    if not matches:
        return summary, []
    rows = []
    for match in matches[:MAX_ROWS]:
        licenses = ", ".join(str(lic) for lic in _list(match.get("licenses"))) or "—"
        origin = " ".join(str(match.get(k)) for k in ("vendor", "component", "version") if match.get(k))
        rows.append([
            md(match.get("path", "—")), md(match.get("match", "—")),
            f"{match.get('matched_percent')}%" if match.get("matched_percent") is not None else "—",
            link(match.get("url"), origin or None) if match.get("url") else md(origin or "—"),
            md(licenses),
        ])
    lines = ["", *table(["Archivo", "Tipo", "Coincidencia", "Proyecto de origen", "Licencias"], rows)]
    if len(matches) > MAX_ROWS:
        lines.append(f"\n… y {len(matches) - MAX_ROWS} más en `scanoss.json`.")
    return summary, lines


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _dolos(sources: Sources, team_id: str, names: dict[str, str]) -> tuple[str, list[str], float]:
    rows = sources.data.get("dolos/pairs.csv")
    if rows is None:
        return sources.note("dolos/pairs.csv", "acr dolos"), [], 0.0
    pairs = []
    for row in rows:
        if team_id not in (row.get("team_a"), row.get("team_b")):
            continue
        mine_a = row.get("team_a") == team_id
        other = row.get("team_b") if mine_a else row.get("team_a")
        pairs.append({
            "other": other or "—",
            "similarity": _number(row.get("similarity")),
            "file_pairs": int(_number(row.get("file_pairs"))),
            "mine": row.get("file_a") if mine_a else row.get("file_b"),
            "theirs": row.get("file_b") if mine_a else row.get("file_a"),
        })
    if not pairs:
        return "Este equipo no se comparó con Dolos (se necesitan al menos dos equipos obtenidos).", [], 0.0
    pairs.sort(key=lambda p: (-p["similarity"], p["other"]))
    similar = [p for p in pairs if p["file_pairs"]]
    top = pairs[0]["similarity"]
    if not similar:
        return f"Sin archivos similares con los {len(pairs)} equipo(s) comparados.", [], 0.0
    summary = (
        f"similitud máxima {percent(top)} con {md(names.get(similar[0]['other'], similar[0]['other']))}; "
        f"{len(similar)} de {len(pairs)} equipo(s) con archivos similares"
    )
    lines = ["", *table(
        ["Equipo", "Similitud", "Pares de archivos", "Archivo propio", "Archivo del otro equipo"],
        (
            [md(names.get(p["other"], p["other"])), percent(p["similarity"]), str(p["file_pairs"]),
             md(p["mine"] or "—"), md(p["theirs"] or "—")]
            for p in similar[:MAX_ROWS]
        ),
    )]
    return summary, lines, top


def _git(sources: Sources, team_id: str) -> tuple[str, dict[str, Any]]:
    signals = sources.data.get("git-signals.json")
    if signals is None:
        return sources.note("git-signals.json", f"acr git-signals {team_id}"), {}
    if not signals.get("has_git"):
        return "la entrega no trae historial de git (carpeta o ZIP sin `.git`).", {}
    outside = _dict(signals.get("outside_event"))
    first = _dict(signals.get("first_commit"))
    parts = [f"{signals.get('commit_count', 0)} commit(s)"]
    if first.get("author_date"):
        parts.append(f"primero el {md(first['author_date'])}")
    if signals.get("single_commit"):
        parts.append("**un solo commit con todo el código**")
    parts.append(
        f"{outside.get('before_start', 0)} antes del inicio y {outside.get('after_end', 0)} "
        "después del cierre del evento"
    )
    return "; ".join(parts), {"git": signals, "before": outside.get("before_start", 0)}


# --------------------------------------------------------------------------- report


def build_report(
    team: Team,
    event: Event,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    *,
    names: dict[str, str] | None = None,
    dashboard_url: str = DEFAULT_DASHBOARD_URL,
    now: datetime | None = None,
) -> str:
    """The Markdown report of ``team`` with the template of SPEC.md "Entregables"."""
    if not is_valid_team_id(team.id):
        raise ReportError(f"id inválido ({team.id!r})")
    names = names or {}
    sources = load_sources(team.id, reports_dir)
    stamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")

    languages, language_table = _languages(sources, team.id)
    security, facts = _security(sources, team.id)
    scanoss, scanoss_lines = _scanoss(sources, team.id)
    dolos, dolos_lines, top_similarity = _dolos(sources, team.id, names)
    git, git_facts = _git(sources, team.id)
    has_review = "review.json" in sources.data
    has_web = "web-plagiarism.json" in sources.data

    critical = []
    if facts.get("fixable"):
        critical.append(f"{len(facts['fixable'])} CVE crítica(s)/alta(s) con corrección disponible")
    if facts.get("secrets"):
        critical.append(f"{len(facts['secrets'])} secreto(s) expuesto(s)")
    semgrep_errors = [f for f in facts.get("semgrep", []) if f.level == "error"]
    if semgrep_errors:
        critical.append(f"{len(semgrep_errors)} hallazgo(s) de código inseguro de nivel error (Semgrep)")
    unknown = {"scc.json", "megalinter.sarif", "trivy.sarif", "semgrep.sarif"} & (
        sources.missing | sources.unreadable
    )
    critical_text = "; ".join(critical) if critical else "ninguno en los resultados disponibles"
    if unknown:
        critical_text += f" (faltan {', '.join(f'`{n}`' for n in sorted(unknown))})"

    review_note = (
        "Ver `review.json` (revisión asistida con Claude Code); su formato llega con PB-13."
        if has_review
        else "Pendiente: revisión asistida con Claude Code (`review.json`, PB-13)."
    )
    dashboard = link(dashboard_url) if dashboard_url else "—"

    out = [
        f"# Reporte de revisión – {md(team.nombre)} – {md(team.proyecto)}",
        "",
        f"Evento: {md(event.nombre)} · Equipo: {md(team.id)} · Generado: {stamp}",
        "",
        "> Borrador generado a partir de las herramientas de análisis estático. Una coincidencia "
        "indica similitud, no copia confirmada; toda conclusión la valida el revisor.",
        "",
        "## Resumen",
        f"- Lenguajes y tamaño (scc): {languages}",
        f"- Enlace al dashboard (Grafana): {dashboard} (equipo `{team.id}`)",
        f"- Hallazgos críticos: {critical_text}",
        "",
        "## Factibilidad técnica",
        "",
        f"Objetivo del proyecto: {md(team.objetivo)}",
        "",
        *(language_table + [""] if language_table else [languages, ""]),
    ]
    if "vulns" in facts:
        out += [f"Dependencias con CVE (Trivy): {len({v.package for v in facts['vulns']})} paquete(s).", ""]
    out += [
        f"Arquitectura y dependencias abandonadas: {review_note}",
        "",
        "## Calidad del código",
        "",
        *_quality(sources, team.id),
        "",
        "## Seguridad",
        *security,
        "",
        "## Integración responsable de IA",
        "",
    ]
    if "secrets" in facts:
        out.append(f"- Secretos expuestos (API keys en el código): {len(facts['secrets'])}.")
    out += [
        f"- Checklist de IA responsable y riesgos de OWASP Top 10 para LLM: {review_note}",
        "",
        "## Plagio",
        f"- Código externo (SCANOSS): {scanoss}",
        *scanoss_lines,
        f"- Entre equipos (Dolos): {dolos}",
        *dolos_lines,
        "- Ideas en la web: "
        + (
            "ver `web-plagiarism.json`; su formato llega con PB-13."
            if has_web
            else "Pendiente: búsqueda web con Claude Code (`web-plagiarism.json`, PB-13)."
        ),
        f"- Historial de git: {git}",
        "",
        "## Recomendaciones al equipo",
        "",
    ]
    out += _recommendations(facts, scanoss_lines, top_similarity, git_facts) or [
        "- Sin recomendaciones automáticas con los resultados disponibles."
    ]
    if not has_review:
        out.append("- Las recomendaciones de la revisión asistida se agregan cuando exista `review.json`.")
    return "\n".join(out).rstrip() + "\n"


def _recommendations(
    facts: dict[str, Any], scanoss_lines: list[str], top_similarity: float, git: dict[str, Any]
) -> list[str]:
    items = []
    if facts.get("secrets"):
        items.append(
            "- Revocar y rotar los secretos expuestos, quitarlos del código y del historial de git, "
            "y leerlos de variables de entorno."
        )
    if facts.get("fixable"):
        packages = sorted({v.package for v in facts["fixable"]})
        shown = ", ".join(md(p) for p in packages[:5]) + (" …" if len(packages) > 5 else "")
        items.append(
            f"- Actualizar las dependencias con CVE críticas/altas que ya tienen corrección: {shown}."
        )
    if facts.get("semgrep"):
        items.append(
            "- Corregir los patrones inseguros señalados por Semgrep, empezando por los de nivel error."
        )
    if scanoss_lines:
        items.append("- Citar el origen y respetar la licencia del código tomado de proyectos open source.")
    if top_similarity or git.get("before"):
        items.append(
            "- Aclarar con los organizadores el código similar a otros equipos o anterior al evento."
        )
    return items


def write_report(
    team: Team,
    event: Event,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    *,
    names: dict[str, str] | None = None,
    dashboard_url: str = DEFAULT_DASHBOARD_URL,
    now: datetime | None = None,
) -> Path:
    """Write ``reports_dir/<id>/reporte.md``; the previous report is replaced only when done."""
    text = build_report(team, event, reports_dir, names=names, dashboard_url=dashboard_url, now=now)
    out_dir = reports_dir / team.id
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / REPORT_FILE
    staging = out_dir / f".{REPORT_FILE}.tmp"
    staging.write_text(text, encoding="utf-8")
    staging.replace(target)
    return target
