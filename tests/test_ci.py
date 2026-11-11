"""Tests for ``acr ci``, the GitHub Action mode (PB-62, PB-65; SPEC.md "GitHub Action para otros
repositorios").

The tools are simulated: no git, scc, container engine, SCANOSS or network.
"""

import json
from pathlib import Path

import pytest

from acr import ci
from acr.ai_review import AiReviewResult
from acr.ci import CiError, main_ci, parse_datetime, parse_members, should_fail, team_id_for
from acr.cli import main
from acr.git_signals import build_signals
from acr.megalinter import MegaLinterResult
from acr.report import FAIL, PASS, Mark

SECRET = "ghp_" + "Z" * 36


def sarif(tool: str, path: str, snippet: str | None = None) -> dict:
    region = {"startLine": 3}
    if snippet:
        region["snippet"] = {"text": snippet}
    result = {"ruleId": "rule-1", "message": {"text": "found"},
              "locations": [{"physicalLocation": {"artifactLocation": {"uri": f"/tmp/lint/{path}"},
                                                  "region": region}}]}
    return {"runs": [{"tool": {"driver": {"name": tool}}, "results": [result]}]}


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "work" / "My.Repo"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    (repo / ".git").mkdir()
    return repo


@pytest.fixture
def tools(monkeypatch):
    """Fake steps; ``calls`` records what ran and ``secret`` makes Betterleaks find one."""
    state = {"calls": [], "secret": False}

    def git_signals(team_id, event, repos_dir, reports_dir, members=None):
        state["calls"].append("git")
        signals = build_signals(team_id, event, [], members)
        path = reports_dir / team_id / "git-signals.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(signals), encoding="utf-8")
        return path, signals

    def scc(team_id, repos_dir, reports_dir):
        state["calls"].append("scc")
        path = reports_dir / team_id / "scc.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"totals": {"languages": 1, "files": 1, "code": 1},
                                    "languages": [{"name": "Markdown", "code": 1}]}), encoding="utf-8")
        return path, {}

    def megalinter(team_id, repos_dir, reports_dir, **kwargs):
        state["calls"].append(("megalinter", kwargs["engine"]))
        assert (repos_dir / team_id / "README.md").is_file()
        out = reports_dir / team_id
        out.mkdir(parents=True, exist_ok=True)
        data = sarif("betterleaks", "config.py", snippet=f"token = '{SECRET}'")
        if not state["secret"]:
            data["runs"][0]["results"] = []
        (out / "megalinter.sarif").write_text(json.dumps(data), encoding="utf-8")
        semgrep = sarif("semgrep", "a.py", snippet=SECRET)
        (out / "semgrep.sarif").write_text(json.dumps(semgrep), encoding="utf-8")
        return MegaLinterResult(returncode=0)

    def scanoss(team_id, repos_dir, reports_dir):
        state["calls"].append("scanoss")
        return reports_dir / team_id / "scanoss.json", {}

    monkeypatch.setattr(ci, "write_git_signals", git_signals)
    monkeypatch.setattr(ci, "write_scc", scc)
    monkeypatch.setattr(ci, "run_megalinter", megalinter)
    monkeypatch.setattr(ci, "write_scanoss", scanoss)
    return state


def env_for(tmp_path, workspace, **extra):
    summary, output = tmp_path / "summary.md", tmp_path / "output.txt"
    env = {
        "GITHUB_WORKSPACE": str(workspace),
        "RUNNER_TEMP": str(tmp_path / "runner-temp"),
        "GITHUB_REPOSITORY": "someone/My.Repo",
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_OUTPUT": str(output),
    }
    env.update(extra)
    return env, summary, output


def outputs(path: Path) -> dict:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


# --------------------------------------------------------------------------- inputs


@pytest.mark.parametrize(
    ("repository", "expected"),
    [("owner/My.Repo", "my-repo"), ("owner/__x__", "x"), ("owner/---", "repo"), ("Proyecto Ñ", "proyecto")],
)
def test_team_id_is_safe_for_paths(repository, expected):
    assert team_id_for(repository) == expected


def test_parse_members():
    members = parse_members("analopez: Ana López\n@beto\n\n# comment\n")
    assert [(m.github, m.nombre) for m in members] == [("analopez", "Ana López"), ("beto", "beto")]
    assert parse_members("") is None
    with pytest.raises(CiError, match="línea 1"):
        parse_members("not valid!: X")


def test_dates_need_a_time_zone():
    assert parse_datetime("", "event-start") is None
    with pytest.raises(CiError, match="zona horaria"):
        parse_datetime("2026-11-01T09:00:00", "event-start")
    with pytest.raises(CiError, match="mal formada"):
        parse_datetime("ayer", "event-start")


@pytest.mark.parametrize(
    "extra",
    [
        {"ACR_EVENT_START": "2026-11-01T09:00:00-06:00"},
        {"ACR_EVENT_START": "2026-11-03T09:00:00-06:00", "ACR_EVENT_END": "2026-11-01T09:00:00-06:00"},
        {"ACR_FAIL_ON": "always"},
        {"ACR_SCANOSS": "maybe"},
        {"ACR_ENGINE": "lxc"},
        {"ACR_AI_MAX_INPUT_CHARS": "lots"},
    ],
)
def test_invalid_inputs_exit_2(tmp_path, workspace, tools, extra):
    env, summary, _ = env_for(tmp_path, workspace, **extra)
    assert main_ci(env, log=lambda *_: None) == 2
    assert "Invalid input" in summary.read_text(encoding="utf-8")
    assert tools["calls"] == []


def test_out_is_required_outside_github(workspace):
    with pytest.raises(CiError, match="--out"):
        ci.options_from_env({"GITHUB_WORKSPACE": str(workspace)})


def test_out_inside_the_workspace_is_refused(tmp_path, workspace, tools):
    env, _, _ = env_for(tmp_path, workspace)
    assert main_ci(env, out=str(workspace / "reports"), log=lambda *_: None) == 2
    assert not (workspace / "reports").exists()


def test_cli_ci_refuses_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["-c", "otro.yml", "ci"]) == 2
    assert "equipos.yml" in capsys.readouterr().err


# --------------------------------------------------------------------------- run


def test_never_reads_equipos_yml_nor_fetches_other_repositories(tmp_path, workspace, tools, monkeypatch):
    (workspace / "equipos.yml").write_text(
        "evento: {nombre: X}\nequipos:\n  - {id: otro, repo: https://github.com/someone/else}\n",
        encoding="utf-8",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("acr ci must not read equipos.yml or clone repositories")

    monkeypatch.setattr("acr.config.load_config", forbidden)
    monkeypatch.setattr("acr.fetch.clone_git", forbidden)
    env, summary, _ = env_for(tmp_path, workspace)
    assert main_ci(env, log=lambda *_: None) == 0
    out = tmp_path / "runner-temp" / "acr"
    assert [p.name for p in (out / "repos").iterdir()] == ["my-repo"]
    assert "`equipos.yml` was ignored" in summary.read_text(encoding="utf-8")


def test_full_run_writes_summary_outputs_and_artifact(tmp_path, workspace, tools):
    env, summary, output = env_for(tmp_path, workspace, ACR_SCANOSS="false")
    assert main_ci(env, log=lambda *_: None) == 0
    assert tools["calls"] == ["git", "scc", ("megalinter", "docker")]
    text = summary.read_text(encoding="utf-8")
    assert "## Advanced Code Review – someone/My.Repo" in text
    assert "| 4.3 |" in text and "SCANOSS was turned off" in text and "Dolos was skipped" in text
    values = outputs(output)
    assert int(values["pass"]) + int(values["fail"]) + int(values["manual"]) == 14
    assert values["errors"] == "0"
    artifact = Path(values["artifact-dir"])
    assert (artifact / "reporte.md").is_file()
    assert not (artifact / "megalinter").exists()
    assert Path(values["report-path"]).name == "reporte.md"
    assert not (workspace / "reports").exists()


def test_secrets_never_reach_the_summary_or_the_artifact(tmp_path, workspace, tools):
    tools["secret"] = True
    env, summary, output = env_for(tmp_path, workspace)
    main_ci(env, log=lambda *_: None)
    assert SECRET not in summary.read_text(encoding="utf-8")
    artifact = Path(outputs(output)["artifact-dir"])
    for path in artifact.iterdir():
        assert SECRET not in path.read_text(encoding="utf-8"), path.name
    semgrep = json.loads((artifact / "semgrep.sarif").read_text(encoding="utf-8"))
    assert "snippet" not in json.dumps(semgrep)


@pytest.mark.parametrize(("fail_on", "code"), [("none", 0), ("secrets", 1), ("fail", 1)])
def test_fail_on_secrets(tmp_path, workspace, tools, fail_on, code):
    tools["secret"] = True
    env, summary, _ = env_for(tmp_path, workspace, ACR_FAIL_ON=fail_on)
    assert main_ci(env, log=lambda *_: None) == code
    if code:
        assert "item(s) 4.3 failed" in summary.read_text(encoding="utf-8")


def test_fail_on_only_uses_deterministic_items():
    marks = {"1.1": Mark(PASS, ""), "1.2": Mark(PASS, ""), "2.1": Mark(FAIL, ""), "4.3": Mark(PASS, "")}
    assert should_fail(marks, "fail") == []
    marks["1.2"] = Mark(FAIL, "")
    assert should_fail(marks, "fail") == ["1.2"]
    assert should_fail(marks, "secrets") == []
    assert should_fail(marks, "none") == []


def test_git_signals_get_fork_shallow_and_no_window(tmp_path, workspace, tools):
    (workspace / ".git" / "shallow").write_text("abc\n", encoding="utf-8")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"repository": {"fork": True}}), encoding="utf-8")
    env, summary, _ = env_for(tmp_path, workspace, GITHUB_EVENT_PATH=str(event))
    main_ci(env, log=lambda *_: None)
    signals_path = tmp_path / "runner-temp" / "acr" / "reports" / "my-repo" / "git-signals.json"
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    assert signals["fork"] is True
    assert signals["shallow_clone"] is True
    assert signals["outside_event"] is None and signals["event"] is None
    text = summary.read_text(encoding="utf-8")
    assert "Shallow clone" in text and "fetch-depth: 0" in text


def test_sprint_window_is_kept_when_given(tmp_path, workspace, tools):
    env, _, _ = env_for(
        tmp_path, workspace,
        ACR_EVENT_START="2026-10-24T16:30:00-06:00", ACR_EVENT_END="2026-11-05T23:59:00-06:00",
    )
    main_ci(env, log=lambda *_: None)
    path = tmp_path / "runner-temp" / "acr" / "reports" / "my-repo" / "git-signals.json"
    signals = json.loads(path.read_text(encoding="utf-8"))
    assert signals["outside_event"] == {"before_start": 0, "after_end": 0, "commits": []}
    assert signals["event"]["start"].startswith("2026-10-24T16:30")
    report = (path.parent / "reporte.md").read_text(encoding="utf-8")
    assert "before the sprint started" in report


def test_ai_review_off_by_default_even_with_a_key(tmp_path, workspace, tools, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("AI review must be off by default")

    monkeypatch.setattr(ci, "write_ai_review", forbidden)
    env, summary, _ = env_for(tmp_path, workspace, OPENROUTER_API_KEY="sk-or-v1-" + "k" * 30)
    assert main_ci(env, log=lambda *_: None) == 0
    assert "AI review" not in summary.read_text(encoding="utf-8").split("Notes:")[-1]


def test_ai_review_enabled_without_key_is_noted(tmp_path, workspace, tools):
    env, summary, _ = env_for(tmp_path, workspace, ACR_AI_REVIEW="true")
    assert main_ci(env, log=lambda *_: None) == 0
    assert "AI review skipped" in summary.read_text(encoding="utf-8")


def test_ai_review_enabled_with_key(tmp_path, workspace, tools, monkeypatch):
    key = "sk-or-v1-" + "k" * 30
    seen = {}

    def fake(team, event_name, settings, repos_dir, reports_dir):
        seen["model"], seen["team"] = settings.model, team.id
        path = reports_dir / team.id / "review.json"
        return AiReviewResult(path, settings.model, 3, 1200, 900, 150, 0.0002)

    monkeypatch.setattr(ci, "write_ai_review", fake)
    logs = []
    env, summary, _ = env_for(tmp_path, workspace, ACR_AI_REVIEW="true", OPENROUTER_API_KEY=key,
                              ACR_AI_MODEL="deepseek/deepseek-v4.1-flash-1017")
    assert main_ci(env, log=logs.append) == 0
    assert seen == {"model": "deepseek/deepseek-v4.1-flash-1017", "team": "my-repo"}
    text = summary.read_text(encoding="utf-8")
    assert "draft by `deepseek/deepseek-v4.1-flash-1017`" in text and "cost USD 0.0002" in text
    assert key not in text and not any(key in line for line in logs)


def test_a_failing_step_does_not_stop_the_others(tmp_path, workspace, tools, monkeypatch):
    from acr.megalinter import MegaLinterError

    def broken(*args, **kwargs):
        raise MegaLinterError("no se encontró docker")

    monkeypatch.setattr(ci, "run_megalinter", broken)
    env, summary, output = env_for(tmp_path, workspace)
    assert main_ci(env, log=lambda *_: None) == 0
    assert "MegaLinter: failed (no se encontró docker)" in summary.read_text(encoding="utf-8")
    assert outputs(output)["errors"] == "1"
    assert "scanoss" in tools["calls"]
    assert Path(outputs(output)["report-path"]).is_file()
