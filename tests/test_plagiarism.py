"""Plagio page (PB-22, SPEC.md "Dashboard local" > "5. Plagio" and "Detección de plagio").

Covers ``reports/plagiarism.json`` (built from SCANOSS, Dolos, web search and git signals files
in a temporary folder) and the Grafana dashboard that reads it, as data: no Grafana, SCANOSS,
Dolos, git or network.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from acr.cli import EXIT_ERROR, EXIT_OK, main
from acr.plagiarism import (
    PLAGIARISM_FILE,
    PlagiarismError,
    build_plagiarism,
    team_plagiarism,
    write_plagiarism,
)

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "services" / "grafana" / "dashboards" / "plagio.json"
PLAGIARISM_URL = "http://files:8080/reports/plagiarism.json"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
TEAMS = ["equipo-01", "equipo-02", "equipo-03"]
PAIRS_CSV = (
    "team_a,team_b,similarity,file_pairs,longest_fragment,total_overlap,file_a,file_b\n"
    "equipo-01,equipo-02,0.8123,3,40,120,src/a.py,lib/b.py\n"
    "equipo-02,equipo-03,0.1,0,0,0,,\n"
    "equipo-01,equipo-03,0.0,0,0,0,,\n"
)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def scanoss(team_id: str) -> dict:
    return {
        "schema_version": 1,
        "team_id": team_id,
        "tool": "scanoss",
        "totals": {
            "files_scanned": 10, "files_matched": 2, "matched_percent": 20.0,
            "file_matches": 1, "snippet_matches": 1,
        },
        "excluded": {"files": 0, "dirs": []},
        "matches": [
            {
                "path": "src/util.js", "match": "file", "matched_percent": 100.0, "lines": "all",
                "oss_lines": "all", "component": "lodash", "vendor": "lodash", "version": "4.17.21",
                "purl": "pkg:github/lodash/lodash", "licenses": ["MIT"],
                "url": "https://github.com/lodash/lodash", "file_url": None, "source_file": "lodash.js",
            },
            {
                "path": "src/app.js", "match": "snippet", "matched_percent": 35.0, "lines": "1-20",
                "oss_lines": "5-24", "component": "demo", "vendor": None, "version": None,
                "purl": None, "licenses": ["Apache-2.0", "MIT"], "url": "https://github.com/x/demo",
                "file_url": None, "source_file": "demo.js",
            },
        ],
    }


def web(team_id: str, **overrides) -> dict:
    data = {
        "schema_version": 1,
        "team_id": team_id,
        "tool": "claude-code",
        "generated_at": "2026-11-04T10:00:00-06:00",
        "status": "validated",
        "keywords": ["recetas", "inventario"],
        "queries": [{"query": "app recetas inventario hackathon", "source": "devpost"}],
        "matches": [
            {
                "title": "FridgeChef", "url": "https://devpost.com/software/fridgechef",
                "source": "devpost", "published": "2024-05-01", "before_event": True,
                "similarity": "idea", "evidence": "Misma idea: recetas con lo que hay en el refri.",
            },
            {
                "title": "Recipe tutorial", "url": "https://example.org/tutorial",
                "source": "tutorial", "published": None, "before_event": None,
                "similarity": "text", "evidence": "README con párrafos iguales.",
            },
        ],
    }
    data.update(overrides)
    return data


def git_signals(team_id: str, commits: int = 1, before: int = 1) -> dict:
    return {
        "schema_version": 1,
        "team_id": team_id,
        "event": {"start": "2026-11-01T09:00:00-06:00", "end": "2026-11-03T18:00:00-06:00"},
        "has_git": True,
        "commit_count": commits,
        "single_commit": commits == 1,
        "first_commit": {
            "hash": "a" * 40,
            "author_date": "2026-10-30T12:00:00-06:00",
            "committer_date": "2026-10-30T12:00:00-06:00",
        },
        "last_commit": None,
        "outside_event": {"before_start": before, "after_end": 0, "commits": []},
    }


def all_reports(reports: Path) -> None:
    write_json(reports / "equipo-01" / "scanoss.json", scanoss("equipo-01"))
    write_json(reports / "equipo-01" / "web-plagiarism.json", web("equipo-01"))
    write_json(reports / "equipo-01" / "git-signals.json", git_signals("equipo-01"))
    write_json(reports / "equipo-02" / "git-signals.json", git_signals("equipo-02", commits=12, before=0))
    (reports / "dolos").mkdir(parents=True, exist_ok=True)
    (reports / "dolos" / "pairs.csv").write_text(PAIRS_CSV, encoding="utf-8")


# --- plagiarism.json --------------------------------------------------------------------------


def test_scanoss_matches_per_file(tmp_path):
    all_reports(tmp_path)
    data = team_plagiarism("equipo-01", tmp_path)
    [summary] = data["teams"]
    assert summary["scanoss"] is True
    assert (summary["files_scanned"], summary["files_matched"], summary["matched_percent"]) == (10, 2, 20.0)
    assert summary["external_matches"] == 2
    assert data["external_code"][0] == {
        "team": "equipo-01", "file": "src/util.js", "match": "file", "matched_percent": 100.0,
        "project": "lodash lodash 4.17.21", "licenses": "MIT", "url": "https://github.com/lodash/lodash",
    }
    assert data["external_code"][1]["project"] == "demo"
    assert data["external_code"][1]["licenses"] == "Apache-2.0, MIT"


def test_web_matches(tmp_path):
    all_reports(tmp_path)
    data = team_plagiarism("equipo-01", tmp_path)
    [summary] = data["teams"]
    assert (summary["web"], summary["web_status"], summary["web_matches"], summary["web_before_event"]) == (
        True, "validated", 2, 1,
    )
    assert data["web_matches"][0] == {
        "team": "equipo-01", "title": "FridgeChef", "url": "https://devpost.com/software/fridgechef",
        "source": "devpost", "published": "2024-05-01", "before_event": True, "similarity": "idea",
        "status": "validated",
    }
    assert data["web_matches"][1]["published"] == "" and data["web_matches"][1]["before_event"] is None


@pytest.mark.parametrize(
    "bad",
    [
        web("equipo-02"),  # belongs to another team
        web("equipo-01", status="whatever"),
        web("equipo-01", keywords=["token ghp_" + "A" * 36]),  # a secret in clear
    ],
)
def test_invalid_web_file_is_left_out(tmp_path, bad):
    write_json(tmp_path / "equipo-01" / "web-plagiarism.json", bad)
    data = team_plagiarism("equipo-01", tmp_path)
    assert data["teams"][0]["web"] is False
    assert data["web_matches"] == []


def test_git_signals(tmp_path):
    all_reports(tmp_path)
    [summary] = team_plagiarism("equipo-01", tmp_path)["teams"]
    assert summary["git_signals"] is True and summary["has_git"] is True
    assert (summary["commit_count"], summary["single_commit"]) == (1, True)
    assert (summary["before_start"], summary["after_end"]) == (1, 0)
    assert summary["first_commit"] == "2026-10-30T12:00:00-06:00"
    assert summary["fork"] is None  # git-signals.json does not detect forks: unknown, not "no"


def test_fork_is_read_when_present(tmp_path):
    write_json(tmp_path / "equipo-01" / "git-signals.json", {**git_signals("equipo-01"), "fork": True})
    assert team_plagiarism("equipo-01", tmp_path)["teams"][0]["fork"] is True


def test_dolos_matrix_in_both_directions(tmp_path):
    all_reports(tmp_path)
    data = build_plagiarism(TEAMS, tmp_path, now=NOW)
    cells = {(r["team_a"], r["team_b"]): r for r in data["similarity"]}
    assert set(cells) == {(a, b) for a in TEAMS for b in TEAMS if a != b}
    assert cells[("equipo-01", "equipo-02")]["similarity"] == 0.8123
    assert cells[("equipo-02", "equipo-01")]["similarity"] == 0.8123
    # Files are swapped so that file_a is always from the row team.
    assert (cells[("equipo-01", "equipo-02")]["file_a"], cells[("equipo-01", "equipo-02")]["file_b"]) == (
        "src/a.py", "lib/b.py",
    )
    assert (cells[("equipo-02", "equipo-01")]["file_a"], cells[("equipo-02", "equipo-01")]["file_b"]) == (
        "lib/b.py", "src/a.py",
    )
    # Each pair once, with "similar" only where Dolos found similar files.
    assert [(p["team_a"], p["team_b"], p["similar"]) for p in data["team_pairs"]] == [
        ("equipo-01", "equipo-02", 1), ("equipo-02", "equipo-03", 0), ("equipo-01", "equipo-03", 0),
    ]
    teams = {t["team"]: t for t in data["teams"]}
    first = teams["equipo-01"]
    assert (first["dolos"], first["similar_teams"], first["max_similarity"]) == (True, 1, 0.8123)
    assert (teams["equipo-03"]["similar_teams"], teams["equipo-03"]["max_similarity"]) == (0, 0.1)


def test_dolos_pairs_of_unknown_teams_are_ignored(tmp_path):
    all_reports(tmp_path)
    data = build_plagiarism(["equipo-01", "equipo-03"], tmp_path, now=NOW)
    assert {(r["team_a"], r["team_b"]) for r in data["similarity"]} == {
        ("equipo-01", "equipo-03"), ("equipo-03", "equipo-01"),
    }
    assert len(data["team_pairs"]) == 1


def test_missing_or_unreadable_files_give_empty_team(tmp_path):
    (tmp_path / "equipo-02").mkdir()
    (tmp_path / "equipo-02" / "scanoss.json").write_text("{not json", encoding="utf-8")
    data = build_plagiarism(["equipo-02"], tmp_path, now=NOW)
    [summary] = data["teams"]
    assert (summary["scanoss"], summary["dolos"], summary["web"], summary["git_signals"]) == (
        False, False, False, False,
    )
    assert summary["external_matches"] == summary["web_matches"] == summary["commit_count"] == 0
    assert data["similarity"] == data["team_pairs"] == data["external_code"] == data["web_matches"] == []


@pytest.mark.parametrize("team_id", ["../x", "A", ""])
def test_rejects_invalid_team_id(tmp_path, team_id):
    with pytest.raises(PlagiarismError):
        team_plagiarism(team_id, tmp_path)


def test_build_and_write_all_teams(tmp_path):
    all_reports(tmp_path)
    data = build_plagiarism(TEAMS, tmp_path, now=NOW)
    assert data["schema_version"] == 1 and data["generated_at"] == "2026-11-06T12:00:00+00:00"
    assert [t["team"] for t in data["teams"]] == TEAMS
    path, written = write_plagiarism(TEAMS, tmp_path, now=NOW)
    assert path == tmp_path / PLAGIARISM_FILE
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


def test_cli_plagiarism(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    all_reports(tmp_path / "reports")
    write_json(tmp_path / "reports" / "equipo-03" / "web-plagiarism.json", web("equipo-01"))  # invalid
    config = write_config(tmp_path, TEAMS)
    assert main(["--config", str(config), "plagiarism"]) == EXIT_OK
    captured = capsys.readouterr()
    assert (
        "Plagio: 2 coincidencia(s) con código externo, 1 par(es) de equipos similares y "
        "2 proyecto(s) parecido(s) en la web en 3 equipo(s) -> reports/plagiarism.json"
    ) in captured.out
    assert "sin scanoss.json para equipo-02, equipo-03" in captured.err
    assert "sin git-signals.json para equipo-03" in captured.err
    assert "web-plagiarism.json no válido para equipo-03" in captured.err
    data = json.loads((tmp_path / "reports" / PLAGIARISM_FILE).read_text(encoding="utf-8"))
    assert [t["team"] for t in data["teams"]] == TEAMS


def test_cli_plagiarism_warns_without_dolos(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = write_config(tmp_path, ["equipo-01", "equipo-02"])
    assert main(["--config", str(config), "plagiarism"]) == EXIT_OK
    assert "sin datos de Dolos" in capsys.readouterr().err


def test_cli_plagiarism_invalid_config(tmp_path):
    assert main(["--config", str(tmp_path / "nope.yml"), "plagiarism"]) == EXIT_ERROR


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
        "Coincidencias con código externo": "stat",
        "Pares de equipos similares": "stat",
        "Proyectos parecidos en la web": "stat",
        "Similitud entre equipos": "table",
        "Código externo por equipo": "barchart",
        "Código externo": "table",
        "Ideas en la web": "table",
        "Historial de git": "table",
    }


def test_every_panel_reads_plagiarism_json_filtered_by_equipo(dashboard, tmp_path):
    all_reports(tmp_path)
    data = build_plagiarism(TEAMS, tmp_path, now=NOW)
    for title, panel in data_panels(dashboard).items():
        target = query(panel)
        assert target["datasource"]["uid"] == "acr-infinity", title
        assert target["url"] == PLAGIARISM_URL, title
        assert target["parser"] == "backend", title
        assert "IN (${equipo:singlequote})" in target["filterExpression"], title
        # Every selector exists in the rows that acr plagiarism writes.
        rows = data[target["root_selector"]]
        assert rows, title
        for column in target["columns"]:
            assert column["selector"] in rows[0], (title, column["selector"])


def test_cards_sum_their_column(dashboard):
    for title, panel in data_panels(dashboard).items():
        if panel["type"] != "stat":
            continue
        columns = [c["text"] for c in query(panel)["columns"] if c["type"] == "number"]
        (column,) = columns
        reduce = panel["options"]["reduceOptions"]
        assert reduce["calcs"] == ["sum"], title
        assert re.fullmatch(reduce["fields"].strip("/"), column), title


def test_similar_pairs_card_counts_each_pair_once(dashboard):
    target = query(data_panels(dashboard)["Pares de equipos similares"])
    assert target["root_selector"] == "team_pairs"
    assert target["filterExpression"] == (
        "team_a IN (${equipo:singlequote}) || team_b IN (${equipo:singlequote})"
    )


def test_similarity_is_a_team_by_team_matrix_colored_by_percent(dashboard):
    panel = data_panels(dashboard)["Similitud entre equipos"]
    (transformation,) = panel["transformations"]
    assert transformation["id"] == "groupingToMatrix"
    assert transformation["options"]["rowField"] == "team_a"
    assert transformation["options"]["columnField"] == "team_b"
    assert transformation["options"]["valueField"] == "similarity"
    defaults = panel["fieldConfig"]["defaults"]
    assert defaults["unit"] == "percentunit" and (defaults["min"], defaults["max"]) == (0, 1)
    assert defaults["custom"]["cellOptions"]["type"] == "color-background"


def test_external_code_has_origin_license_and_link(dashboard):
    texts = [c["text"] for c in query(data_panels(dashboard)["Código externo"])["columns"]]
    for column in ("Archivo", "Proyecto de origen", "Licencias", "Enlace"):
        assert column in texts
    bars = query(data_panels(dashboard)["Código externo por equipo"])
    assert [c["selector"] for c in bars["columns"]] == ["team", "matched_percent"]


def test_web_table_has_link_date_and_similarity_level(dashboard):
    panel = data_panels(dashboard)["Ideas en la web"]
    selectors = [c["selector"] for c in query(panel)["columns"]]
    for selector in ("team", "title", "url", "published", "similarity"):
        assert selector in selectors
    levels = next(
        prop["value"][0]["options"]
        for override in panel["fieldConfig"]["overrides"]
        if override["matcher"]["options"] == "Nivel de parecido"
        for prop in override["properties"]
        if prop["id"] == "mappings"
    )
    assert {key: value["text"] for key, value in levels.items()} == {
        "idea": "idea", "structure": "estructura", "text": "texto",
    }


def test_git_table_has_the_three_signals(dashboard):
    selectors = [c["selector"] for c in query(data_panels(dashboard)["Historial de git"])["columns"]]
    for selector in ("before_start", "single_commit", "fork"):
        assert selector in selectors


def test_team_cells_open_the_equipo_page(dashboard):
    for title, panel in data_panels(dashboard).items():
        if panel["type"] == "stat":
            continue
        links = [
            link["url"]
            for override in panel["fieldConfig"]["overrides"]
            if override["matcher"]["options"] in ("Equipo", "team_a\\team_b")
            for prop in override["properties"]
            if prop["id"] == "links"
            for link in prop["value"]
        ]
        assert links == ["/d/acr-equipo?var-equipo=${__value.raw}"], title
