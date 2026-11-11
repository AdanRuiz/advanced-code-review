"""Team report in English following the official checklist (PB-14, PB-59, SPEC.md "Entregables").

Builds ``reports/<id>/reporte.md`` with the English template of SPEC.md "Entregables" from every
result available in ``reports/``, and ``reports/judge-questions.md`` with the questions for the
judges of every team:

- Summary: languages and size (``scc.json``), checklist totals and key findings.
- Official checklist: items 1.1 to 4.3 marked Pass / Fail / Manual review with their evidence.
  1.1 and 1.2 come from ``git-signals.json`` (PB-57), 4.3 from Betterleaks (``megalinter.sarif``),
  2.3 and 4.2 are always manual review, and the rest from ``review.json`` (PB-58).
- Plagiarism and originality evidence: ``scanoss.json``, ``dolos/pairs.csv``,
  ``web-plagiarism.json``, ``git-signals.json`` and the external code classification of
  ``review.json``.
- Questions for the judges (``review.json``), Reviewer feedback (written by the reviewer, with
  draft points) and a technical annex that is not scored: CVE (``trivy.sarif``), insecure code
  (``semgrep.sarif``), code quality grade A–E (``megalinter.sarif``) and responsible AI.

Every automatic mark is a draft the reviewer validates. ``review.json`` and
``web-plagiarism.json`` are used only when they pass ``acr review-validate``. A missing or
unreadable result never stops the report: its section says which file is missing and which
command creates it. Detected secrets are never written: only their rule, file and line
(AGENTS.md, invariant 6). Text from the team's repository or the assisted review (paths, rule
ids, URLs, notes) is escaped so it cannot inject Markdown or HTML into the report or its PDF.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from acr.config import Event, Team, is_valid_team_id
from acr.review import (
    OFFICIAL_CHECKLIST,
    REVIEW_FILE,
    WEB_PLAGIARISM_FILE,
    validate_review,
    validate_web_plagiarism,
)

DEFAULT_REPORTS_DIR = Path("reports")
DEFAULT_DASHBOARD_URL = "http://localhost:3000"
REPORT_FILE = "reporte.md"
JUDGE_QUESTIONS_FILE = "judge-questions.md"
MAX_ROWS = 20
MAX_EVIDENCE = 5
# Point 1.1: a commit with at least this % of the added code is a bulk upload to explain.
BULK_COMMIT_PERCENT = 50.0
# ``acr ci`` (PB-62): the checkout had only part of the history.
SHALLOW_NOTE = "Shallow clone: the git history is incomplete; check out with `fetch-depth: 0`."

# MegaLinter runs the linters on the repository mounted here (acr.megalinter.WORKSPACE).
_WORKSPACE_PREFIXES = ("/tmp/lint/", "tmp/lint/")
_SECRET_TOOLS = ("betterleaks", "gitleaks")
_SECURITY_TOOLS = (*_SECRET_TOOLS, "trivy", "semgrep")
_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
_CWE_RE = re.compile(r"\bCWE-\d+\b")
_OWASP_RE = re.compile(r"\bA\d{2}:\d{4}\b(?:\s*-\s*[^,;\]]+)?")
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|~])")

# SPEC.md "Checklist oficial de code review": every item, in order, in English.
CHECKLIST_ITEMS: dict[str, str] = {
    "1.1": "Commits show meaningful iteration, with no unexplained bulk uploads",
    "1.2": "Evidence of team collaboration",
    "1.3": OFFICIAL_CHECKLIST["1.3"],
    "2.1": OFFICIAL_CHECKLIST["2.1"],
    "2.2": OFFICIAL_CHECKLIST["2.2"],
    "2.3": "Team members can explain their code and decisions",
    "2.4": OFFICIAL_CHECKLIST["2.4"],
    "3.1": OFFICIAL_CHECKLIST["3.1"],
    "3.2": OFFICIAL_CHECKLIST["3.2"],
    "3.3": OFFICIAL_CHECKLIST["3.3"],
    "3.4": OFFICIAL_CHECKLIST["3.4"],
    "4.1": OFFICIAL_CHECKLIST["4.1"],
    "4.2": "Design and UX consider the user",
    "4.3": "No API keys, secrets, credentials or private data in the repository",
}
PASS, FAIL, MANUAL = "Pass", "Fail", "Manual review"
_RESULTS = {"pass": PASS, "fail": FAIL, "manual_review": MANUAL}

JUDGE_TOPICS: dict[str, str] = {
    "2.3": "2.3 Explaining the code",
    "4.1": "4.1 Code vs demo",
    "4.2": "4.2 Design and UX",
    "purpose": "AI purpose",
    "autonomy": "AI autonomy",
    "privacy": "Privacy",
    "bias_and_limits": "Bias and limits",
    "accessibility": "Accessibility",
    "human_support": "Human support",
}
EXTERNAL_ORIGINS: dict[str, str] = {
    "official_template": "Official template (allowed)",
    "generator": "Project generator (to review)",
    "third_party_cited": "Third-party, cited (allowed)",
    "third_party_uncited": "Third-party, not cited (item 2.4)",
    "ai_declared": "AI-generated, declared (allowed)",
    "ai_undeclared": "AI-generated, not declared (item 3.4)",
}
DOCUMENT_STATUS = {
    "read": "read",
    "not_provided": "not provided",
    "url_only": "only a URL, PDF pending",
    "unreadable": "could not be read",
}
AI_CHECKS: dict[str, str] = {
    "data_sent_to_provider": "User data (minors) sent to the AI provider",
    "data_retention": "Conversations or personal data stored (where, how long)",
    "api_keys_outside_code": "API keys outside the code (environment variables)",
    "input_output_filtering": "Input and output filtering or moderation",
    "failure_handling": "Handling of AI failures, delays or inappropriate answers",
    "ai_disclosure": "Users know they are talking to an AI",
    "adult_supervision": "Adult supervision (parent, teacher)",
    "system_prompts_reviewable": "System prompts in the code and reviewable",
}
OWASP_LLM_RISKS: dict[str, str] = {
    "LLM01": "LLM01 Prompt injection",
    "LLM02": "LLM02 Sensitive information disclosure",
    "LLM05": "LLM05 Improper output handling",
    "LLM06": "LLM06 Excessive agency",
    "LLM10": "LLM10 Unbounded consumption",
}
CHECK_STATUS = {"pass": "Pass", "fail": "Fail", "not_applicable": "N/A", "unknown": "Unknown"}


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


def _yes_no(value: Any) -> str:
    return "Yes" if value is True else "No" if value is False else "Unknown"


# --------------------------------------------------------------------------- loading results


@dataclass
class Sources:
    """Results read from ``reports/``; ``missing``, ``unreadable`` and ``invalid`` list file names."""

    data: dict[str, Any] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)
    unreadable: set[str] = field(default_factory=set)
    invalid: dict[str, int] = field(default_factory=dict)

    def note(self, name: str, command: str) -> str:
        """Why a section has no data, and how to get it."""
        if name in self.invalid:
            return (
                f"No data: `{name}` has {self.invalid[name]} validation error(s); "
                f"fix it and run `{command}`."
            )
        if name in self.unreadable:
            return f"No data: `{name}` could not be read; run `{command}` again."
        return f"No data: `{name}` is missing; run `{command}`."


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


def _read_validated(
    path: Path, sources: Sources, key: str, team_id: str, validate: Callable[..., list[str]]
) -> None:
    """The assisted review files are used only when they pass ``acr review-validate``."""
    _read_json(path, sources, key)
    if key not in sources.data:
        return
    errors = validate(sources.data[key], team_id)
    if errors:
        del sources.data[key]
        sources.invalid[key] = len(errors)


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
    _read_validated(team_dir / REVIEW_FILE, sources, REVIEW_FILE, team_id, validate_review)
    _read_validated(
        team_dir / WEB_PLAGIARISM_FILE, sources, WEB_PLAGIARISM_FILE, team_id, validate_web_plagiarism
    )
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


# --------------------------------------------------------------------------- checklist


@dataclass(frozen=True)
class Mark:
    """The proposed result of one checklist item and its evidence (already escaped Markdown)."""

    result: str
    evidence: str


def _locations(items: Any) -> str:
    places = [
        md(f"{loc.get('file')}:{loc.get('line')}") for loc in map(_dict, _list(items)) if loc.get("file")
    ]
    if not places:
        return ""
    extra = f" (+{len(places) - MAX_EVIDENCE} more)" if len(places) > MAX_EVIDENCE else ""
    return " Evidence: " + ", ".join(places[:MAX_EVIDENCE]) + extra + "."


def _mark_commits(sources: Sources, team_id: str) -> Mark:
    """1.1: number of commits and share of the added code in the largest one (git signals)."""
    signals = sources.data.get("git-signals.json")
    if signals is None:
        return Mark(MANUAL, sources.note("git-signals.json", f"acr git-signals {team_id}"))
    if signals.get("shallow_clone"):
        return Mark(MANUAL, SHALLOW_NOTE)
    if not signals.get("has_git"):
        return Mark(MANUAL, "The submission has no git history (folder or ZIP without `.git`).")
    count = signals.get("commit_count") or 0
    if not count:
        return Mark(MANUAL, "The repository has no commits.")
    largest = _dict(signals.get("largest_commit"))
    share = signals.get("largest_commit_percent")
    where = ""
    if largest.get("hash"):
        where = f" (commit {md(str(largest['hash'])[:7])}, {md(largest.get('author_date', '—'))})"
    if signals.get("single_commit"):
        return Mark(FAIL, f"A single commit holds all the code{where}.")
    if share is None:
        return Mark(MANUAL, f"{count} commits, but no lines of code were added.")
    text = f"{count} commits; the largest commit holds {share}% of the added code{where}."
    if share >= BULK_COMMIT_PERCENT:
        return Mark(MANUAL, f"{text} Check whether this bulk upload is explained.")
    return Mark(PASS, text)


def _mark_collaboration(sources: Sources, team_id: str) -> Mark:
    """1.2: distinct authors and commits per author, compared with ``integrantes``."""
    signals = sources.data.get("git-signals.json")
    if signals is None:
        return Mark(MANUAL, sources.note("git-signals.json", f"acr git-signals {team_id}"))
    if signals.get("shallow_clone"):
        return Mark(MANUAL, SHALLOW_NOTE)
    if not signals.get("has_git") or not signals.get("author_count"):
        return Mark(MANUAL, "No commit authors to compare (no git history or no commits).")
    authors = [_dict(a) for a in _list(signals.get("authors"))]
    listed = ", ".join(
        f"{md(a.get('github') or a.get('name') or '—')} ({a.get('commits', 0)})" for a in authors[:MAX_ROWS]
    )
    text = f"{signals['author_count']} author(s), commits per author: {listed}."
    if signals.get("single_author"):
        return Mark(FAIL, f"{text} A single author is an alert: teams have 2 or more members.")
    if signals.get("members") is None:
        return Mark(PASS, f"{text} `integrantes` is not listed in `equipos.yml`; authors not matched.")
    without = [md(login) for login in _list(signals.get("members_without_commits"))]
    outsiders = signals.get("authors_not_members") or 0
    if outsiders:
        text += f" {outsiders} author(s) are not listed as members."
    if without:
        return Mark(MANUAL, f"{text} Members without commits: {', '.join(without)}.")
    return Mark(PASS, f"{text} Every member has commits.")


def _mark_secrets(sources: Sources, team_id: str) -> Mark:
    """4.3: Betterleaks findings (git history included); never the value."""
    sarif = sources.data.get("megalinter.sarif")
    if sarif is None:
        return Mark(MANUAL, sources.note("megalinter.sarif", f"acr megalinter {team_id}"))
    if not any(_is_tool(name, _SECRET_TOOLS) for name in sarif_tools(sarif)):
        return Mark(MANUAL, "Betterleaks did not run in MegaLinter; check for secrets manually.")
    secrets = _secrets(sources) or []
    if not secrets:
        return Mark(PASS, "Betterleaks found no secrets in the code or the git history.")
    places = ", ".join(f"{md(s.rule)} at {md(s.where)}" for s in secrets[:MAX_EVIDENCE])
    extra = f" (+{len(secrets) - MAX_EVIDENCE} more)" if len(secrets) > MAX_EVIDENCE else ""
    return Mark(FAIL, f"{len(secrets)} secret(s) found by Betterleaks (values hidden): {places}{extra}.")


def _document_note(review: dict[str, Any], key: str, label: str) -> str:
    document = _dict(_dict(review.get("documents")).get(key))
    if not document:
        return ""
    status = DOCUMENT_STATUS.get(str(document.get("status")), md(document.get("status")))
    return f" {label}: {status}; {md(document.get('notes', ''))}"


def _pending_review(sources: Sources, team_id: str) -> str:
    """Why ``review.json`` is not used: missing (run review-prompt) or invalid (review-validate)."""
    command = "acr review-validate" if REVIEW_FILE in sources.invalid else "acr review-prompt"
    return "Pending assisted review. " + sources.note(REVIEW_FILE, f"{command} {team_id}")


def _review_marks(sources: Sources, team_id: str) -> dict[str, Mark]:
    review = sources.data.get(REVIEW_FILE)
    if review is None:
        return {item: Mark(MANUAL, _pending_review(sources, team_id)) for item in OFFICIAL_CHECKLIST}
    marks = {}
    for item in map(_dict, _list(review.get("official_checklist"))):
        evidence = md(item.get("notes", "")) + _locations(item.get("evidence"))
        marks[str(item.get("id"))] = Mark(_RESULTS.get(str(item.get("result")), MANUAL), evidence)
    documents = (("3.4", "prompts_pdf", "AI prompts PDF"), ("4.1", "presentation", "Presentation"))
    for item_id, key, label in documents:
        if item_id in marks:
            extra = _document_note(review, key, label)
            marks[item_id] = Mark(marks[item_id].result, marks[item_id].evidence + extra)
    if "2.4" in marks:
        marks["2.4"] = Mark(
            marks["2.4"].result, marks["2.4"].evidence + " See *Plagiarism and originality evidence*."
        )
    return marks


def checklist(sources: Sources, team_id: str) -> dict[str, Mark]:
    """Proposed result of items 1.1 to 4.3, in the order of the official checklist."""
    marks = _review_marks(sources, team_id)
    marks["1.1"] = _mark_commits(sources, team_id)
    marks["1.2"] = _mark_collaboration(sources, team_id)
    marks["4.3"] = _mark_secrets(sources, team_id)
    for item in ("2.3", "4.2"):
        marks[item] = Mark(
            MANUAL, "Requires an interview or the demo; see *Questions for the judges*."
        )
    return {item: marks.get(item, Mark(MANUAL, "No evidence.")) for item in CHECKLIST_ITEMS}


# --------------------------------------------------------------------------- sections


def _languages(sources: Sources, team_id: str) -> str:
    scc = sources.data.get("scc.json")
    if scc is None:
        return sources.note("scc.json", f"acr scc {team_id}")
    totals = _dict(scc.get("totals"))
    languages = [_dict(lang) for lang in _list(scc.get("languages"))]
    names = ", ".join(md(lang.get("name")) for lang in languages[:5]) or "none recognized"
    return f"{names}; {totals.get('code', 0)} lines of code in {totals.get('files', 0)} file(s)"


def secret_findings(sarif: dict[str, Any]) -> list[Finding]:
    """Betterleaks (or Gitleaks) results of ``megalinter.sarif``: rule, file and line, never the value."""
    return [f for f in sarif_findings(sarif) if _is_tool(f.tool, _SECRET_TOOLS)]


def _secrets(sources: Sources) -> list[Finding] | None:
    sarif = sources.data.get("megalinter.sarif")
    if sarif is None:
        return None
    return secret_findings(sarif)


def quality_grade(clean: int, warnings_only: int, total: int) -> tuple[float, str]:
    """SPEC.md "6. Calificación": 100 × (clean + 0.5 × non-blocking) / total, A ≥ 90 … E < 50."""
    score = 100 * (clean + 0.5 * warnings_only) / total if total else 0.0
    for limit, grade in ((90, "A"), (80, "B"), (65, "C"), (50, "D")):
        if score >= limit:
            return score, grade
    return score, "E"


def _quality(sources: Sources, team_id: str) -> list[str]:
    sarif = sources.data.get("megalinter.sarif")
    if sarif is None:
        return ["- Code quality (MegaLinter grade A–E): " + sources.note(
            "megalinter.sarif", f"acr megalinter {team_id}")]
    tools = sorted({name for name in sarif_tools(sarif) if not _is_tool(name, _SECURITY_TOOLS)})
    if not tools:
        return ["- Code quality (MegaLinter grade A–E): MegaLinter ran no quality linters for "
                "the languages of this repository."]
    findings = [f for f in sarif_findings(sarif) if not _is_tool(f.tool, _SECURITY_TOOLS)]
    errors = Counter(f.tool for f in findings if f.level == "error")
    others = Counter(f.tool for f in findings if f.level != "error")
    clean = [name for name in tools if not errors[name] and not others[name]]
    warnings_only = [name for name in tools if not errors[name] and others[name]]
    score, grade = quality_grade(len(clean), len(warnings_only), len(tools))
    lines = [
        f"- Code quality (MegaLinter grade A–E): **{grade}** ({score:.0f}/100): {len(tools)} "
        f"linter(s), {len(clean)} clean, {len(warnings_only)} with warnings only, "
        f"{len(tools) - len(clean) - len(warnings_only)} with errors; "
        f"{sum(errors.values())} error(s) and {sum(others.values())} warning(s) in total.",
        "",
    ]
    ordered = sorted(tools, key=lambda name: (-errors[name], -others[name], name.lower()))
    lines += table(
        ["Linter", "Errors", "Warnings"],
        ([md(name), str(errors[name]), str(others[name])] for name in ordered),
    )
    files = Counter(f.path for f in findings)
    if files:
        lines += ["", "Files with the most findings:", ""]
        lines += table(["File", "Findings"], ([md(p), str(n)] for p, n in files.most_common(10)))
    return lines


def _dependencies(sources: Sources, team_id: str) -> tuple[list[str], list[Vulnerability]]:
    trivy = sources.data.get("trivy.sarif")
    label = "- Dependencies with known vulnerabilities (CVE, fixed version): "
    if trivy is None:
        return [label + sources.note("trivy.sarif", f"acr megalinter {team_id}")], []
    vulns = vulnerabilities(trivy)
    fixable = fixable_critical(vulns)
    by_severity = Counter(v.severity for v in vulns)
    breakdown = ", ".join(f"{by_severity[s]} {s.lower()}" for s in _SEVERITIES if by_severity[s]) or "none"
    lines = [
        f"{label}**{len(fixable)}** critical/high CVE with a fix available "
        f"({len(vulns)} CVE in {len({v.package for v in vulns})} package(s): {breakdown})."
    ]
    if fixable:
        lines += ["", *table(
            ["CVE", "Package", "Installed", "Fixed", "CVSS", "Severity"],
            (
                [md(v.cve), md(v.package), md(v.installed), md(v.fixed),
                 f"{v.cvss:.1f}" if v.cvss is not None else "—", v.severity.lower()]
                for v in fixable[:MAX_ROWS]
            ),
        )]
        if len(fixable) > MAX_ROWS:
            lines.append(f"\n… and {len(fixable) - MAX_ROWS} more in `trivy.sarif`.")
    return lines, fixable


def _insecure_code(sources: Sources, team_id: str) -> tuple[list[str], list[Finding]]:
    semgrep = sources.data.get("semgrep.sarif")
    label = "- Insecure code patterns (CWE / OWASP Top 10): "
    if semgrep is None:
        return [label + sources.note("semgrep.sarif", f"acr megalinter {team_id}")], []
    findings = sarif_findings(semgrep)
    categories: Counter[str] = Counter()
    for finding in findings:
        cwe, owasp = weakness_tags(finding)
        categories.update(cwe + owasp or ["unclassified"])
    top = ", ".join(f"{md(name)} ({count})" for name, count in categories.most_common(5))
    errors = sum(1 for f in findings if f.level == "error")
    lines = [
        f"{label}**{len(findings)}** Semgrep finding(s), {errors} at error level"
        + (f"; categories: {top}." if top else ".")
    ]
    if findings:
        ranked = sorted(findings, key=lambda f: (f.level != "error", f.path, f.line or 0))
        lines += ["", *table(
            ["Rule", "CWE", "OWASP", "Level", "Location"],
            (
                [md(f.rule), md(", ".join(weakness_tags(f)[0]) or "—"),
                 md(", ".join(weakness_tags(f)[1]) or "—"), md(f.level), md(f.where)]
                for f in ranked[:MAX_ROWS]
            ),
        )]
        if len(findings) > MAX_ROWS:
            lines.append(f"\n… and {len(findings) - MAX_ROWS} more in `semgrep.sarif`.")
    return lines, findings


def _responsible_ai(sources: Sources, team_id: str) -> list[str]:
    label = "- Responsible AI integration and OWASP LLM: "
    review = sources.data.get(REVIEW_FILE)
    if review is None:
        return [label + _pending_review(sources, team_id)]
    ai = _dict(review.get("ai_checklist"))
    providers = ", ".join(md(p) for p in _list(ai.get("providers"))) or "none listed"
    uses = f"the project uses AI (providers: {providers})." if ai.get("uses_ai") else (
        "the project does not integrate an AI model or provider."
    )
    lines = [label + uses, ""]
    rows = []
    groups = ((_list(ai.get("items")), AI_CHECKS), (_list(review.get("owasp_llm")), OWASP_LLM_RISKS))
    for checks, labels in groups:
        for check in map(_dict, checks):
            check_id = str(check.get("id"))
            rows.append([
                labels.get(check_id, md(check_id)),
                CHECK_STATUS.get(str(check.get("status")), md(check.get("status"))),
                md(check.get("notes", "")) + _locations(check.get("evidence")),
            ])
    return lines + table(["Check", "Status", "Notes"], rows)


def _review_findings(sources: Sources) -> list[str]:
    review = sources.data.get(REVIEW_FILE)
    findings = [_dict(f) for f in _list(review.get("findings"))] if review else []
    if not findings:
        return []
    lines = ["- Other findings of the assisted review:", ""]
    lines += table(
        ["Id", "Criterion", "Severity", "Location", "Finding", "Recommendation"],
        (
            [md(f.get("id")), md(f.get("criterion")), md(f.get("severity")),
             md(f"{f.get('file')}:{f.get('line')}"), md(f.get("title", "")),
             md(f.get("recommendation") or "—")]
            for f in findings[:MAX_ROWS]
        ),
    )
    if len(findings) > MAX_ROWS:
        lines.append(f"\n… and {len(findings) - MAX_ROWS} more in `review.json`.")
    return lines


def _cited(path: str, external: list[dict[str, Any]]) -> str:
    """Whether a SCANOSS match is cited, from the external code classification of ``review.json``."""
    for item in external:
        target = str(item.get("path", "")).rstrip("/")
        if path == target or path.startswith(target + "/"):
            origin = item.get("origin")
            if origin in ("third_party_cited", "official_template"):
                return "Yes"
            if origin == "ai_declared":
                # Declared AI use is not a citation of the matched project: say so for the judges.
                return "AI-declared"
            if origin in ("third_party_uncited", "ai_undeclared"):
                return "No"
            return "Generator"
    return "Unknown"


def _scanoss(sources: Sources, team_id: str) -> tuple[str, list[str]]:
    report = sources.data.get("scanoss.json")
    if report is None:
        return sources.note("scanoss.json", f"acr scanoss {team_id}"), []
    totals = _dict(report.get("totals"))
    summary = (
        f"{totals.get('files_matched', 0)} of {totals.get('files_scanned', 0)} file(s) with matches "
        f"({totals.get('matched_percent', 0)}%): {totals.get('file_matches', 0)} whole-file and "
        f"{totals.get('snippet_matches', 0)} snippet match(es)"
    )
    matches = [_dict(m) for m in _list(report.get("matches"))]
    if not matches:
        return summary, []
    review = sources.data.get(REVIEW_FILE)
    external = [_dict(i) for i in _list(review.get("external_code"))] if review else []
    rows = []
    for match in matches[:MAX_ROWS]:
        licenses = ", ".join(str(lic) for lic in _list(match.get("licenses"))) or "—"
        origin = " ".join(str(match.get(k)) for k in ("vendor", "component", "version") if match.get(k))
        rows.append([
            md(match.get("path", "—")), md(match.get("match", "—")),
            f"{match.get('matched_percent')}%" if match.get("matched_percent") is not None else "—",
            link(match.get("url"), origin or None) if match.get("url") else md(origin or "—"),
            md(licenses),
            _cited(str(match.get("path", "")), external) if review else "Pending review",
        ])
    lines = ["", *table(["File", "Type", "Match", "Source project", "License", "Cited"], rows)]
    if len(matches) > MAX_ROWS:
        lines.append(f"\n… and {len(matches) - MAX_ROWS} more in `scanoss.json`.")
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
        return "This team was not compared with Dolos (it needs at least two fetched teams).", [], 0.0
    pairs.sort(key=lambda p: (-p["similarity"], p["other"]))
    similar = [p for p in pairs if p["file_pairs"]]
    if not similar:
        return f"No similar files with the {len(pairs)} team(s) compared.", [], 0.0
    top = similar[0]["similarity"]
    summary = (
        f"highest similarity {percent(top)} with {md(names.get(similar[0]['other'], similar[0]['other']))}; "
        f"{len(similar)} of {len(pairs)} team(s) with similar files"
    )
    lines = ["", *table(
        ["Team", "Similarity", "File pairs", "Own file", "Other team's file"],
        (
            [md(names.get(p["other"], p["other"])), percent(p["similarity"]), str(p["file_pairs"]),
             md(p["mine"] or "—"), md(p["theirs"] or "—")]
            for p in similar[:MAX_ROWS]
        ),
    )]
    return summary, lines, top


def _web(sources: Sources, team_id: str) -> tuple[str, list[str]]:
    data = sources.data.get(WEB_PLAGIARISM_FILE)
    if data is None:
        return "Pending web search. " + sources.note(WEB_PLAGIARISM_FILE, f"acr review-prompt {team_id}"), []
    matches = [_dict(m) for m in _list(data.get("matches"))]
    if not matches:
        return f"no similar projects found ({len(_list(data.get('queries')))} search(es))", []
    before = sum(1 for m in matches if m.get("before_event") is True)
    summary = f"{len(matches)} similar project(s), {before} published before the event"
    lines = ["", *table(
        ["Project", "Source", "Published", "Before the event", "Level"],
        (
            [link(m.get("url"), m.get("title")), md(m.get("source")), md(m.get("published") or "unknown"),
             _yes_no(m.get("before_event")), md(m.get("similarity"))]
            for m in matches[:MAX_ROWS]
        ),
    )]
    return summary, lines


def _git(sources: Sources, team_id: str) -> tuple[str, dict[str, Any]]:
    signals = sources.data.get("git-signals.json")
    if signals is None:
        return sources.note("git-signals.json", f"acr git-signals {team_id}"), {}
    if signals.get("shallow_clone"):
        return SHALLOW_NOTE, {}
    if not signals.get("has_git"):
        return "the submission has no git history (folder or ZIP without `.git`).", {}
    outside = _dict(signals.get("outside_event"))
    first = _dict(signals.get("first_commit"))
    parts = [f"{signals.get('commit_count', 0)} commit(s)"]
    if first.get("author_date"):
        parts.append(f"first on {md(first['author_date'])}")
    if signals.get("outside_event") is None:
        # acr ci without event-start/event-end: there is no sprint window to compare with.
        parts.append("no sprint window given")
    else:
        parts.append(
            f"{outside.get('before_start', 0)} before the sprint started and "
            f"{outside.get('after_end', 0)} after it ended"
        )
    if signals.get("single_commit"):
        parts.append("**single commit with all the code**")
    if signals.get("author_count") is not None:
        single = " (**single author**)" if signals.get("single_author") else ""
        parts.append(f"{signals['author_count']} author(s){single}")
    fork = signals.get("fork")
    parts.append("fork: " + ("unknown (a clone does not say)" if fork is None else _yes_no(fork).lower()))
    return "; ".join(parts), {"before": outside.get("before_start", 0)}


def _external_code(sources: Sources, team_id: str) -> list[str]:
    label = "- Templates, generators and third-party code: "
    review = sources.data.get(REVIEW_FILE)
    if review is None:
        return [label + _pending_review(sources, team_id)]
    items = [_dict(i) for i in _list(review.get("external_code"))]
    if not items:
        return [label + "no external or generated code identified."]
    return [label + f"{len(items)} item(s)", "", *table(
        ["Path", "Origin", "Source", "Notes"],
        (
            [md(i.get("path")), EXTERNAL_ORIGINS.get(str(i.get("origin")), md(i.get("origin"))),
             link(i.get("url"), i.get("source")) if i.get("url") else md(i.get("source") or "—"),
             md(i.get("notes", ""))]
            for i in items[:MAX_ROWS]
        ),
    )]


def judge_questions(sources: Sources) -> list[str]:
    """The questions for the judges of ``review.json``, as a numbered Markdown list."""
    review = sources.data.get(REVIEW_FILE)
    if review is None:
        return []
    lines = []
    for n, question in enumerate(map(_dict, _list(review.get("judge_questions"))), start=1):
        topic = JUDGE_TOPICS.get(str(question.get("topic")), md(question.get("topic")))
        where = md(f"{question.get('file')}:{question.get('line')}")
        lines.append(f"{n}. **{topic}:** {md(question.get('question', ''))} ({where})")
    return lines


def _recommendations(
    facts: dict[str, Any], scanoss_lines: list[str], top_similarity: float, git: dict[str, Any]
) -> list[str]:
    items = []
    if facts.get("secrets"):
        items.append(
            "- Revoke and rotate the exposed secrets, remove them from the code and the git history, "
            "and read them from environment variables."
        )
    if facts.get("fixable"):
        packages = sorted({v.package for v in facts["fixable"]})
        shown = ", ".join(md(p) for p in packages[:5]) + (" …" if len(packages) > 5 else "")
        items.append(f"- Update the dependencies with critical/high CVE that already have a fix: {shown}.")
    if facts.get("semgrep"):
        items.append("- Fix the insecure patterns reported by Semgrep, starting with the error-level ones.")
    if scanoss_lines:
        items.append("- Cite the source and respect the license of code taken from open source projects.")
    if top_similarity or git.get("before"):
        items.append("- Clarify with the organizers the code similar to other teams or older than the event.")
    return items


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
    """The Markdown report of ``team`` with the English template of SPEC.md "Entregables"."""
    if not is_valid_team_id(team.id):
        raise ReportError(f"id inválido ({team.id!r})")
    names = names or {}
    sources = load_sources(team.id, reports_dir)
    stamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")

    marks = checklist(sources, team.id)
    counts = Counter(mark.result for mark in marks.values())
    scanoss, scanoss_lines = _scanoss(sources, team.id)
    dolos, dolos_lines, top_similarity = _dolos(sources, team.id, names)
    web, web_lines = _web(sources, team.id)
    git, git_facts = _git(sources, team.id)
    dependencies, fixable = _dependencies(sources, team.id)
    insecure, semgrep = _insecure_code(sources, team.id)
    facts = {"secrets": _secrets(sources), "fixable": fixable, "semgrep": semgrep}
    review = sources.data.get(REVIEW_FILE)

    failed = [item for item, mark in marks.items() if mark.result == FAIL]
    key = [f"failed items: {', '.join(failed)}" if failed else "no failed items"]
    if fixable:
        key.append(f"{len(fixable)} critical/high CVE with a fix available (annex)")
    if review is not None:
        key.append(f"assisted review ({md(review.get('status'))}): {md(review.get('summary', ''))}")
    unknown = sorted(
        {"scc.json", "megalinter.sarif", "git-signals.json", REVIEW_FILE}
        & (sources.missing | sources.unreadable | set(sources.invalid))
    )
    if unknown:
        key.append("missing results: " + ", ".join(f"`{name}`" for name in unknown))
    dashboard = link(dashboard_url) if dashboard_url else "—"

    out = [
        f"# Code Review Report – {md(team.nombre)} – {md(team.proyecto)}",
        "",
        f"Event: {md(event.nombre)} · Team id: {md(team.id)} · Generated: {stamp} · "
        f"Dashboard: {dashboard}",
        "",
        "> Draft generated from static analysis tools and an assisted review. Every mark is "
        "validated by the reviewer. A match indicates similarity, not confirmed copying; any "
        "sanction is decided by the organizers.",
        "",
        "## Summary",
        f"- Languages and size (scc): {_languages(sources, team.id)}",
        f"- Project goal: {md(team.objetivo)}",
        f"- Checklist: {counts[PASS]} pass · {counts[FAIL]} fail · {counts[MANUAL]} manual review",
        f"- Key findings: {'; '.join(key)}",
        "",
        "## Official checklist",
        "",
        *table(
            ["#", "Item", "Result", "Evidence"],
            (
                [item, CHECKLIST_ITEMS[item], f"**{mark.result}**" if mark.result == FAIL else mark.result,
                 mark.evidence]
                for item, mark in marks.items()
            ),
        ),
        "",
        "## Plagiarism and originality evidence",
        f"- External code (SCANOSS): {scanoss}",
        *scanoss_lines,
        f"- Between teams (Dolos): {dolos}",
        *dolos_lines,
        f"- Similar projects on the web: {web}",
        *web_lines,
        f"- Git history: {git}",
        *_external_code(sources, team.id),
        "",
        "## Questions for the judges",
        "",
    ]
    questions = judge_questions(sources)
    if questions:
        out += questions
    else:
        out.append(_pending_review(sources, team.id))
    out += [
        "",
        "## Reviewer feedback",
        "",
        "> To be written by the reviewer. Draft points from the tools:",
        "",
    ]
    out += _recommendations(facts, scanoss_lines, top_similarity, git_facts) or [
        "- No automatic points with the available results."
    ]
    out += [
        "",
        "## Technical annex (formative, not scored)",
        *dependencies,
        *insecure,
    ]
    if facts["secrets"] is not None:
        out.append(f"- Exposed secrets (item 4.3): {len(facts['secrets'])}, values never shown.")
    out += [*_quality(sources, team.id), *_responsible_ai(sources, team.id), *_review_findings(sources)]
    return "\n".join(out).rstrip() + "\n"


def _write(target: Path, text: str) -> Path:
    """Write ``target``; the previous file is replaced only when done."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.tmp")
    staging.write_text(text, encoding="utf-8")
    staging.replace(target)
    return target


def write_report(
    team: Team,
    event: Event,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    *,
    names: dict[str, str] | None = None,
    dashboard_url: str = DEFAULT_DASHBOARD_URL,
    now: datetime | None = None,
) -> Path:
    """Write ``reports_dir/<id>/reporte.md``."""
    text = build_report(team, event, reports_dir, names=names, dashboard_url=dashboard_url, now=now)
    return _write(reports_dir / team.id / REPORT_FILE, text)


def build_judge_questions(
    teams: Sequence[Team], event: Event, reports_dir: Path = DEFAULT_REPORTS_DIR, now: datetime | None = None
) -> str:
    """``judge-questions.md``: the questions for the judges of every team (SPEC.md "Entregables")."""
    stamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    out = [
        f"# Questions for the judges – {md(event.nombre)}",
        "",
        f"Generated: {stamp}. Draft questions from the assisted review, validated by the reviewer; "
        "each one points to the file and line it is about.",
    ]
    for team in teams:
        if not is_valid_team_id(team.id):
            raise ReportError(f"id inválido ({team.id!r})")
        sources = Sources()
        _read_validated(
            reports_dir / team.id / REVIEW_FILE, sources, REVIEW_FILE, team.id, validate_review
        )
        out += ["", f"## {md(team.nombre)} – {md(team.proyecto)}", ""]
        out += judge_questions(sources) or [_pending_review(sources, team.id)]
    return "\n".join(out).rstrip() + "\n"


def write_judge_questions(
    teams: Sequence[Team], event: Event, reports_dir: Path = DEFAULT_REPORTS_DIR, now: datetime | None = None
) -> Path:
    """Write ``reports_dir/judge-questions.md`` with every team."""
    return _write(reports_dir / JUDGE_QUESTIONS_FILE, build_judge_questions(teams, event, reports_dir, now))
