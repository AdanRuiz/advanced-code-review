"""Tests for the team report in English (PB-14, PB-59, SPEC.md "Entregables")."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.config import Event, Team
from acr.report import (
    CHECKLIST_ITEMS,
    JUDGE_QUESTIONS_FILE,
    REPORT_FILE,
    ReportError,
    build_judge_questions,
    build_report,
    checklist,
    fixable_critical,
    link,
    load_sources,
    md,
    quality_grade,
    sarif_findings,
    vulnerabilities,
    write_judge_questions,
    write_report,
)
from acr.review import AI_CHECKLIST, OFFICIAL_CHECKLIST, OWASP_LLM

EVENT = Event(
    "Hackathon 2026",
    datetime(2026, 11, 1, 9, tzinfo=UTC),
    datetime(2026, 11, 3, 18, tzinfo=UTC),
)
TEAM = Team("equipo-01", "Equipo Alfa", "Proyecto Alfa", "Ayudar a estudiantes", "https://example.org/a.git")
NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
SECRET = "sk-THIS-IS-A-SECRET-VALUE-123"

TEMPLATE_HEADINGS = [
    "# Code Review Report – Equipo Alfa – Proyecto Alfa",
    "## Summary",
    "- Languages and size (scc):",
    "- Checklist:",
    "- Key findings:",
    "## Official checklist",
    "| # | Item | Result | Evidence |",
    "## Plagiarism and originality evidence",
    "- External code (SCANOSS):",
    "- Between teams (Dolos):",
    "- Similar projects on the web:",
    "- Git history:",
    "- Templates, generators and third-party code:",
    "## Questions for the judges",
    "## Reviewer feedback",
    "## Technical annex (formative, not scored)",
    "- Dependencies with known vulnerabilities (CVE, fixed version):",
    "- Insecure code patterns (CWE / OWASP Top 10):",
    "- Code quality (MegaLinter grade A–E):",
    "- Responsible AI integration and OWASP LLM:",
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


def signals(**changes) -> dict:
    data = {
        "has_git": True, "commit_count": 1, "single_commit": True,
        "first_commit": {"author_date": "2026-10-20T10:00:00+00:00"},
        "outside_event": {"before_start": 1, "after_end": 0, "commits": []},
        "largest_commit": {"hash": "abcdef1234", "author_date": "2026-10-20T10:00:00+00:00",
                           "lines_added": 900, "percent_of_code": 100.0},
        "largest_commit_percent": 100.0,
        "author_count": 1, "single_author": True,
        "authors": [{"name": "Ana", "github": "ana", "member": True, "commits": 1}],
        "members": None, "members_without_commits": [], "authors_not_members": 0,
        "fork": None,
    }
    data.update(changes)
    return data


def official(item_id, result="pass", notes="Checked."):
    return {"id": item_id, "result": result, "notes": notes, "evidence": [{"file": "README.md", "line": 1}]}


def check(item_id, status="pass"):
    evidence = [{"file": "src/app.ts", "line": 3}]
    return {"id": item_id, "status": status, "notes": "Reviewed.", "evidence": evidence}


def review_data(team_id="equipo-01") -> dict:
    items = [official(item_id) for item_id in OFFICIAL_CHECKLIST]
    items[1] = official("2.1", "fail", "Lots of lorem ipsum.")
    items[-1] = official("4.1", "manual_review", "Presentation not available.")
    return {
        "schema_version": 2,
        "team_id": team_id,
        "tool": "claude-code",
        "generated_at": "2026-11-04T10:00:00-06:00",
        "status": "draft",
        "summary": "Feasible project with one uncited library.",
        "official_checklist": items,
        "external_code": [
            {"path": "src", "origin": "third_party_uncited", "source": "lodash", "notes": "Not cited."},
            {"path": "web", "origin": "generator", "source": "create-next-app", "notes": "Scaffold."},
        ],
        "documents": {
            "prompts_pdf": {"status": "read", "notes": "Declares ChatGPT for src/api/."},
            "presentation": {"status": "url_only", "notes": "Only a URL."},
        },
        "judge_questions": [
            {"topic": "2.3", "question": "Why did you split the API?", "file": "src/api.ts", "line": 10},
            {"topic": "4.2", "question": "How do children navigate?", "file": "src/ui.tsx", "line": 5},
            {"topic": "privacy", "question": "Which data reaches OpenAI?", "file": "src/chat.ts", "line": 42},
        ],
        "findings": [
            {"id": "F1", "criterion": "responsible_ai", "severity": "high", "file": "src/chat.ts", "line": 42,
             "title": "User input concatenated to the prompt", "description": "Prompt injection.",
             "recommendation": "Separate roles."},
        ],
        "ai_checklist": {"uses_ai": True, "providers": ["OpenAI"],
                         "items": [check(item_id) for item_id in AI_CHECKLIST]},
        "owasp_llm": [check(item_id, "fail") for item_id in OWASP_LLM],
    }


def web_data() -> dict:
    return {
        "schema_version": 1,
        "team_id": "equipo-01",
        "tool": "claude-code",
        "generated_at": "2026-11-04T10:30:00-06:00",
        "status": "draft",
        "keywords": ["study helper"],
        "queries": [{"query": "study helper hackathon", "source": "devpost"}],
        "matches": [{
            "title": "StudyBuddy", "url": "https://devpost.com/software/studybuddy", "source": "devpost",
            "published": "2025-05-10", "before_event": True, "similarity": "idea", "evidence": "Same idea.",
        }],
    }


def full_reports(reports: Path, with_review: bool = True) -> None:
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
        run("Stylelint", [result("x", "/tmp/lint/a.css", 1, level="warning")]),
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
    write_json(team / "git-signals.json", signals())
    dolos = reports / "dolos" / "pairs.csv"
    dolos.parent.mkdir(parents=True, exist_ok=True)
    dolos.write_text(
        "team_a,team_b,similarity,file_pairs,longest_fragment,total_overlap,file_a,file_b\n"
        "equipo-01,equipo-02,0.85,3,40,120,src/app.ts,index.ts\n"
        "equipo-01,equipo-03,0,0,0,0,,\n"
        "equipo-02,equipo-03,0.5,1,10,20,a.py,b.py\n",
        encoding="utf-8",
    )
    if with_review:
        write_json(team / "review.json", review_data())
        write_json(team / "web-plagiarism.json", web_data())


def row(text: str, item: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(f"| {item} |"))


def test_report_follows_english_template_with_all_results(tmp_path):
    full_reports(tmp_path)
    text = build_report(TEAM, EVENT, tmp_path, names={"equipo-02": "Equipo Beta"}, now=NOW)
    positions = [text.index(heading) for heading in TEMPLATE_HEADINGS]
    assert positions == sorted(positions)
    assert "TypeScript, CSS; 900 lines of code in 12 file(s)" in text
    assert "Dashboard: [http://localhost:3000](<http://localhost:3000>)" in text
    assert "Generated: 2026-11-05T12:00:00+00:00" in text
    # Checklist: every item once, in order; 1.1 (single commit), 1.2 (single author), 2.1 and 4.3 fail.
    rows = [line.split(" | ")[0][2:] for line in text.splitlines() if line.startswith("| ") and
            line.split(" | ")[0][2:] in CHECKLIST_ITEMS]
    assert rows == list(CHECKLIST_ITEMS)
    assert "- Checklist: 7 pass · 4 fail · 3 manual review" in text
    assert "- Key findings: failed items: 1.1, 1.2, 2.1, 4.3;" in text
    assert "assisted review (draft): Feasible project with one uncited library." in text
    assert "| **Fail** | A single commit holds all the code (commit abcdef1" in row(text, "1.1")
    assert "A single author is an alert" in row(text, "1.2")
    assert "| **Fail** | Lots of lorem ipsum. Evidence: README.md:1. |" in row(text, "2.1")
    assert "| Manual review | Requires an interview or the demo" in row(text, "2.3")
    assert "AI prompts PDF: read; Declares ChatGPT for src/api/." in row(text, "3.4")
    assert "Presentation: only a URL, PDF pending; Only a URL." in row(text, "4.1")
    assert "1 secret(s) found by Betterleaks (values hidden): generic-api-key at .env:2." in row(text, "4.3")
    # Plagiarism and originality evidence.
    assert "1 of 10 file(s) with matches (10.0%)" in text
    lodash = "[lodash lodash 4.17.21](<https://github.com/lodash/lodash>)"
    assert f"| src/util.ts | file | 100.0% | {lodash} | MIT | No |" in text  # folder "src" is uncited
    assert "highest similarity 85% with Equipo Beta; 1 of 2 team(s) with similar files" in text
    study = "[StudyBuddy](<https://devpost.com/software/studybuddy>)"
    assert f"| {study} | devpost | 2025-05-10 | Yes | idea |" in text
    assert "1 before the sprint started" in text and "**single commit with all the code**" in text
    assert "| web | Project generator (to review) | create-next-app | Scaffold. |" in text
    # Questions, feedback and annex.
    assert "1. **2.3 Explaining the code:** Why did you split the API? (src/api.ts:10)" in text
    assert "3. **Privacy:** Which data reaches OpenAI? (src/chat.ts:42)" in text
    assert "> To be written by the reviewer." in text and "Revoke and rotate the exposed secrets" in text
    assert "**1** critical/high CVE with a fix available (3 CVE in 3 package(s)" in text
    assert "| CVE-2021-1 | lodash | 4.17.15 | 4.17.21 | 7.5 | high |" in text
    assert "CWE-89 (1)" in text and "A03:2021 - Injection (1)" in text
    # Quality: ESLint errors, Stylelint warnings only, Prettier clean -> 50/100, D.
    assert "**D** (50/100): 3 linter(s), 1 clean, 1 with warnings only, 1 with errors" in text
    assert "Trivy |" not in text.split("Code quality")[1].split("Responsible AI")[0]
    assert "the project uses AI (providers: OpenAI)." in text
    assert "| LLM01 Prompt injection | Fail | Reviewed. Evidence: src/app.ts:3. |" in text
    assert "| F1 | responsible\\_ai | high | src/chat.ts:42 | User input concatenated to the prompt |" in text


def test_report_never_contains_secret_values(tmp_path):
    full_reports(tmp_path)
    assert SECRET not in build_report(TEAM, EVENT, tmp_path, now=NOW)


def test_report_without_results_says_what_is_missing(tmp_path):
    text = build_report(TEAM, EVENT, tmp_path, now=NOW)
    for heading in TEMPLATE_HEADINGS:
        assert heading in text
    assert "- Checklist: 0 pass · 0 fail · 14 manual review" in text
    assert "No data: `scc.json` is missing; run `acr scc equipo-01`." in text
    assert "No data: `trivy.sarif` is missing; run `acr megalinter equipo-01`." in text
    assert "No data: `scanoss.json` is missing; run `acr scanoss equipo-01`." in text
    assert "No data: `dolos/pairs.csv` is missing; run `acr dolos`." in text
    assert "No data: `git-signals.json` is missing; run `acr git-signals equipo-01`." in text
    pending = "Pending assisted review. No data: `review.json` is missing; run `acr review-prompt equipo-01`."
    assert pending in text
    assert "Pending web search." in text
    assert "missing results: `git-signals.json`, `megalinter.sarif`, `review.json`, `scc.json`" in text
    assert "No automatic points with the available results." in text


def test_invalid_review_files_are_not_used(tmp_path):
    bad = review_data()
    bad["official_checklist"] = bad["official_checklist"][:-1]  # 4.1 missing
    write_json(tmp_path / "equipo-01" / "review.json", bad)
    write_json(tmp_path / "equipo-01" / "web-plagiarism.json", {"schema_version": 1})
    text = build_report(TEAM, EVENT, tmp_path, now=NOW)
    assert "`review.json` has 1 validation error(s); fix it and run `acr review-validate equipo-01`." in text
    assert "`web-plagiarism.json` has" in text
    assert "Why did you split the API?" not in text
    assert "| Manual review | Pending assisted review." in row(text, "2.1")


def test_review_with_another_team_id_is_rejected(tmp_path):
    write_json(tmp_path / "equipo-01" / "review.json", review_data("equipo-02"))
    assert "review.json" in load_sources("equipo-01", tmp_path).invalid


@pytest.mark.parametrize(
    ("changes", "result", "text"),
    [
        ({"has_git": False}, "Manual review", "no git history"),
        ({"single_commit": False, "commit_count": 5, "largest_commit_percent": 70.0},
         "Manual review", "5 commits; the largest commit holds 70.0% of the added code (commit abcdef1"),
        ({"single_commit": False, "commit_count": 5, "largest_commit_percent": 20.0}, "Pass", "holds 20.0%"),
    ],
)
def test_commit_iteration_item(tmp_path, changes, result, text):
    write_json(tmp_path / "equipo-01" / "git-signals.json", signals(**changes))
    mark = checklist(load_sources("equipo-01", tmp_path), "equipo-01")["1.1"]
    assert mark.result == result and text in mark.evidence


def test_collaboration_item(tmp_path):
    authors = [{"name": "Ana", "github": "ana", "member": True, "commits": 4},
               {"name": "Luis", "github": None, "member": False, "commits": 1}]
    base = {"author_count": 2, "single_author": False, "authors": authors}
    path = tmp_path / "equipo-01" / "git-signals.json"

    write_json(path, signals(**base))
    mark = checklist(load_sources("equipo-01", tmp_path), "equipo-01")["1.2"]
    assert mark.result == "Pass" and "ana (4), Luis (1)" in mark.evidence and "not listed" in mark.evidence

    members = [{"name": "Ana", "github": "ana", "commits": 4}, {"name": "Eva", "github": "eva", "commits": 0}]
    write_json(path, signals(**base, members=members, members_without_commits=["eva"], authors_not_members=1))
    mark = checklist(load_sources("equipo-01", tmp_path), "equipo-01")["1.2"]
    assert mark.result == "Manual review"
    assert "1 author(s) are not listed as members. Members without commits: eva." in mark.evidence

    write_json(path, signals(**base, members=members[:1]))
    assert checklist(load_sources("equipo-01", tmp_path), "equipo-01")["1.2"].result == "Pass"


def test_secrets_item_when_betterleaks_did_not_run_or_found_nothing(tmp_path):
    path = tmp_path / "equipo-01" / "megalinter.sarif"
    write_json(path, {"runs": [run("ESLint", [])]})
    assert checklist(load_sources("equipo-01", tmp_path), "equipo-01")["4.3"].result == "Manual review"
    write_json(path, {"runs": [run("Betterleaks", [])]})
    assert checklist(load_sources("equipo-01", tmp_path), "equipo-01")["4.3"].result == "Pass"


def test_report_handles_unreadable_results(tmp_path):
    team = tmp_path / "equipo-01"
    team.mkdir()
    (team / "scc.json").write_text("{not json", encoding="utf-8")
    (team / "megalinter.sarif").write_text("[]", encoding="utf-8")
    text = build_report(TEAM, EVENT, tmp_path, now=NOW)
    assert "`scc.json` could not be read" in text
    assert "`megalinter.sarif` could not be read" in text


def test_report_without_git_history_and_single_team_in_dolos(tmp_path):
    write_json(tmp_path / "equipo-01" / "git-signals.json", {"has_git": False})
    (tmp_path / "dolos").mkdir()
    (tmp_path / "dolos" / "pairs.csv").write_text("team_a,team_b,similarity\n", encoding="utf-8")
    text = build_report(TEAM, EVENT, tmp_path, now=NOW)
    assert "the submission has no git history" in text
    assert "was not compared with Dolos" in text


def test_untrusted_text_is_escaped(tmp_path):
    team = Team("equipo-01", "<script>alert(1)</script>", "A | B", "o", "r")
    write_json(tmp_path / "equipo-01" / "scanoss.json", {
        "totals": {}, "matches": [{"path": "x|<img src=x>.js", "url": "javascript:alert(1)",
                                   "component": "c"}],
    })
    data = review_data()
    data["summary"] = "<img src=x onerror=alert(1)> [click](javascript:alert(1))"
    data["judge_questions"][0]["question"] = "| injected | row |"
    write_json(tmp_path / "equipo-01" / "review.json", data)
    text = build_report(team, EVENT, tmp_path, now=NOW)
    assert "<script>" not in text and "<img" not in text
    assert "&lt;script&gt;" in text and "A \\| B" in text
    assert "](<javascript" not in text and "](javascript" not in text
    assert "\\| injected \\| row \\|" in text


def test_quality_grade_bands():
    assert quality_grade(9, 0, 10) == (90.0, "A")
    assert quality_grade(8, 0, 10)[1] == "B"
    assert quality_grade(6, 1, 10) == (65.0, "C")
    assert quality_grade(5, 0, 10)[1] == "D"
    assert quality_grade(0, 1, 10)[1] == "E"
    assert quality_grade(0, 0, 0) == (0.0, "E")


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
    assert path.read_text(encoding="utf-8").startswith("# Code Review Report – Equipo Alfa")
    assert not (tmp_path / "equipo-01" / f".{REPORT_FILE}.tmp").exists()
    with pytest.raises(ReportError):
        build_report(Team("../x", "n", "p", "o", "r"), EVENT, tmp_path)


def test_judge_questions_for_every_team(tmp_path):
    full_reports(tmp_path)
    other = Team("equipo-02", "Equipo Beta", "Proyecto Beta", "o", "r")
    text = build_judge_questions([TEAM, other], EVENT, tmp_path, now=NOW)
    assert text.startswith("# Questions for the judges – Hackathon 2026")
    alfa, beta = text.split("## Equipo Alfa – Proyecto Alfa")[1].split("## Equipo Beta – Proyecto Beta")
    assert "1. **2.3 Explaining the code:** Why did you split the API? (src/api.ts:10)" in alfa
    assert "2. **4.2 Design and UX:** How do children navigate? (src/ui.tsx:5)" in alfa
    assert "No data: `review.json` is missing; run `acr review-prompt equipo-02`." in beta
    path = write_judge_questions([TEAM, other], EVENT, tmp_path, now=NOW)
    assert path == tmp_path / JUDGE_QUESTIONS_FILE and path.read_text(encoding="utf-8") == text
    with pytest.raises(ReportError):
        build_judge_questions([Team("../x", "n", "p", "o", "r")], EVENT, tmp_path)


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
    assert "Preguntas para el jurado -> reports/judge-questions.md" in out
    text = (tmp_path / "reports" / "equipo-01" / REPORT_FILE).read_text(encoding="utf-8")
    assert "http://localhost:3001" in text and "with N equipo-02" in text
    assert (tmp_path / "reports" / "equipo-02" / REPORT_FILE).is_file()
    questions = (tmp_path / "reports" / JUDGE_QUESTIONS_FILE).read_text(encoding="utf-8")
    assert "## N equipo-01 – P" in questions and "## N equipo-02 – P" in questions
    assert main(["--config", str(config), "report", "equipo-02"]) == EXIT_OK


def test_cli_report_unknown_team_and_bad_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = write_config(tmp_path, ["equipo-01"])
    assert main(["--config", str(config), "report", "otro"]) == EXIT_ERROR
    assert main(["--config", str(tmp_path / "nope.yml"), "report"]) == EXIT_ERROR
