"""Tests for the AI review through OpenRouter (PB-63, SPEC.md "Revisión con IA vía OpenRouter").

No network: OpenRouter is simulated with ``httpx.MockTransport``.
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from acr import ai_review
from acr.ai_review import (
    DEFAULT_MODEL,
    AiReviewError,
    Settings,
    collect_files,
    parse_answer,
    write_ai_review,
)
from acr.cli import EXIT_ERROR, main
from acr.config import Team
from acr.review import AI_CHECKLIST, OFFICIAL_CHECKLIST, OWASP_LLM, REVIEW_FILE, validate_review

TZ = timezone(timedelta(hours=-6))
NOW = datetime(2026, 11, 11, 10, tzinfo=TZ)
KEY = "sk-or-v1-0123456789abcdefghijklmnopqrstuvwxyz"
TEAM = Team(id="equipo-01", nombre="Alfa", proyecto="Tutor", objetivo="Reading", repo="x")
EVIDENCE = {"file": "README.md", "line": 1}
ON = Settings(enabled=True, api_key=KEY, model=DEFAULT_MODEL, max_input_chars=200_000)


def answer() -> dict:
    """What the model answers: the review without the fields the program sets."""
    def check(item_id):
        return {"id": item_id, "status": "unknown", "notes": "Not visible.", "evidence": []}

    return {
        "summary": "Small project.",
        "official_checklist": [
            {"id": i, "result": "manual_review", "notes": "Checked.", "evidence": [EVIDENCE]}
            for i in OFFICIAL_CHECKLIST
        ],
        "external_code": [],
        "judge_questions": [
            {"topic": "2.3", "question": "Why?", "file": "app.py", "line": 1},
            {"topic": "4.1", "question": "Demo?", "file": "app.py", "line": 1},
            {"topic": "privacy", "question": "Data?", "file": "app.py", "line": 1},
        ],
        "findings": [
            {"id": "F1", "criterion": "quality", "severity": "low", "file": "app.py", "line": 1,
             "title": "Tiny", "description": "Short.", "source": "claude-code"}
        ],
        "ai_checklist": {"uses_ai": False, "providers": [], "items": [check(i) for i in AI_CHECKLIST]},
        "owasp_llm": [check(i) for i in OWASP_LLM],
        # The model may try to set fixed fields; they are overwritten.
        "tool": "claude-code",
        "status": "validated",
        "documents": {"prompts_pdf": {"status": "read", "notes": "Fake."}},
    }


def make_repo(root, team_id="equipo-01"):
    repo = root / "repos" / team_id
    (repo / "src").mkdir(parents=True)
    (repo / "README.md").write_text("# Tutor\nReads books.\n", encoding="utf-8")
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (repo / "src" / "config.py").write_text("TOKEN = 'ghp_" + "a" * 36 + "'\n", encoding="utf-8")
    (repo / ".env").write_text("OPENAI_API_KEY=very-secret-value\n", encoding="utf-8")
    (repo / "leak.py").write_text("password = 'betterleaks-found-me'\n", encoding="utf-8")
    (repo / "node_modules" / "lib").mkdir(parents=True)
    (repo / "node_modules" / "lib" / "index.js").write_text("dependency()\n", encoding="utf-8")
    (repo / "package-lock.json").write_text("{}", encoding="utf-8")
    (repo / "logo.py").write_bytes(b"\x00\x01binary")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    reports = root / "reports" / team_id
    reports.mkdir(parents=True)
    sarif = {"runs": [{"tool": {"driver": {"name": "betterleaks"}}, "results": [
        {"ruleId": "generic-api-key", "locations": [{"physicalLocation": {
            "artifactLocation": {"uri": "/tmp/lint/leak.py"}, "region": {"startLine": 1}}}]}
    ]}]}
    (reports / "megalinter.sarif").write_text(json.dumps(sarif), encoding="utf-8")
    return repo


class FakeOpenRouter:
    def __init__(self, *contents, status=200, body=None):
        self.contents = list(contents)
        self.status = status
        self.body = body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text=self.body or "error")
        content = self.contents.pop(0)
        usage = {"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.0003}
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": usage})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


# --------------------------------------------------------------------------- settings


def test_off_by_default_even_with_a_key():
    settings = Settings.from_env({"OPENROUTER_API_KEY": KEY})
    assert not settings.enabled
    assert "apagada" in settings.skip_reason()


def test_enabled_without_key_is_skipped():
    settings = Settings.from_env({"ACR_AI_REVIEW": "true"})
    assert settings.enabled
    assert "sin key" in settings.skip_reason()


def test_enabled_with_key_uses_default_model():
    settings = Settings.from_env({"ACR_AI_REVIEW": "TRUE", "OPENROUTER_API_KEY": KEY})
    assert settings.skip_reason() is None
    assert settings.model == "deepseek/deepseek-v4.1-flash-1017"
    assert settings.max_input_chars == 200_000


def test_model_and_budget_from_env():
    settings = Settings.from_env({"ACR_AI_MODEL": "x/y", "ACR_AI_MAX_INPUT_CHARS": "5000"})
    assert (settings.model, settings.max_input_chars) == ("x/y", 5000)


@pytest.mark.parametrize("value", ["abc", "0", "-3"])
def test_invalid_budget(value):
    with pytest.raises(AiReviewError):
        Settings.from_env({"ACR_AI_MAX_INPUT_CHARS": value})


# --------------------------------------------------------------------------- what is sent


def test_collect_files_excludes_secrets_dependencies_and_binaries(tmp_path):
    repo = make_repo(tmp_path)
    tree, files = collect_files(repo, {"leak.py"}, 200_000)
    sent = [path for path, _ in files]
    assert sent[0] == "README.md"
    assert "app.py" in sent and "src/config.py" in sent
    for excluded in (".env", "leak.py", "node_modules/lib/index.js", "package-lock.json", "logo.py"):
        assert excluded not in sent
    assert ".env" not in tree and "leak.py" not in tree
    assert not any(path.startswith(".git") for path in tree)
    text = dict(files)["src/config.py"]
    assert "ghp_" not in text and "[REDACTED]" in text
    assert dict(files)["app.py"].startswith("1| print")


def test_collect_files_respects_the_budget(tmp_path):
    repo = make_repo(tmp_path)
    _tree, files = collect_files(repo, set(), 30)
    assert [path for path, _ in files] == ["README.md"]


def test_symlinks_are_not_followed(tmp_path):
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("secret_outside = 1\n", encoding="utf-8")
    try:
        (repo / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("no se pueden crear enlaces simbólicos")
    _tree, files = collect_files(repo, set(), 200_000)
    assert "link.py" not in dict(files)


@pytest.mark.parametrize(
    "content", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here it is: {"a": 1} done']
)
def test_parse_answer(content):
    assert parse_answer(content) == {"a": 1}


def test_parse_answer_rejects_non_objects():
    assert parse_answer("[1, 2]") is None
    assert parse_answer("no json") is None


# --------------------------------------------------------------------------- OpenRouter


def test_writes_a_valid_draft_and_sends_only_allowed_data(tmp_path):
    make_repo(tmp_path)
    fake = FakeOpenRouter(json.dumps(answer()))
    result = write_ai_review(
        TEAM, "Hackathon", ON, tmp_path / "repos", tmp_path / "reports", client=fake.client(), now=NOW
    )
    review = json.loads((tmp_path / "reports" / "equipo-01" / REVIEW_FILE).read_text(encoding="utf-8"))
    assert validate_review(review, "equipo-01") == []
    assert review["tool"] == "openrouter"
    assert review["generated_by"] == DEFAULT_MODEL
    assert review["status"] == "draft"
    assert review["documents"]["prompts_pdf"]["status"] == "not_provided"
    assert review["findings"][0]["source"] == "openrouter"
    assert (result.prompt_tokens, result.completion_tokens, result.cost) == (1000, 200, 0.0003)

    request = fake.requests[0]
    assert request.url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    payload = json.loads(request.content)
    assert payload["model"] == DEFAULT_MODEL
    assert payload["provider"] == {"data_collection": "deny"}
    user = payload["messages"][1]["content"]
    assert user.startswith("<repository_data>") and user.endswith("</repository_data>")
    for secret in ("very-secret-value", "betterleaks-found-me", "ghp_"):
        assert secret not in request.content.decode()
    assert KEY not in json.dumps(review)


def test_invalid_answer_gets_one_retry(tmp_path):
    make_repo(tmp_path)
    broken = answer()
    broken["official_checklist"] = broken["official_checklist"][:2]
    fake = FakeOpenRouter(json.dumps(broken), json.dumps(answer()))
    result = write_ai_review(
        TEAM, "Hackathon", ON, tmp_path / "repos", tmp_path / "reports", client=fake.client(), now=NOW
    )
    assert len(fake.requests) == 2
    retry = json.loads(fake.requests[1].content)["messages"]
    assert retry[-1]["role"] == "user" and "faltan los puntos" in retry[-1]["content"]
    assert result.prompt_tokens == 2000


def test_two_invalid_answers_keep_the_previous_review(tmp_path):
    make_repo(tmp_path)
    previous = tmp_path / "reports" / "equipo-01" / REVIEW_FILE
    previous.write_text("{\"old\": true}", encoding="utf-8")
    fake = FakeOpenRouter("not json", "still not json")
    with pytest.raises(AiReviewError, match="no es válida"):
        write_ai_review(TEAM, "H", ON, tmp_path / "repos", tmp_path / "reports", client=fake.client())
    assert previous.read_text(encoding="utf-8") == "{\"old\": true}"


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (404, '{"error": {"message": "model not found"}}', "no tiene el modelo"),
        (401, "unauthorized", "rechazó la key"),
        (402, "no credits", "saldo"),
        (500, "boom", "código 500"),
    ],
)
def test_http_errors_never_show_the_key(tmp_path, status, body, message):
    make_repo(tmp_path)
    fake = FakeOpenRouter(status=status, body=body)
    with pytest.raises(AiReviewError, match=message) as info:
        write_ai_review(TEAM, "H", ON, tmp_path / "repos", tmp_path / "reports", client=fake.client())
    assert KEY not in str(info.value)


def test_network_error_never_shows_the_key(tmp_path):
    make_repo(tmp_path)

    def down(request):
        raise httpx.ConnectError(f"cannot connect with {KEY}")

    client = httpx.Client(transport=httpx.MockTransport(down))
    with pytest.raises(AiReviewError) as info:
        write_ai_review(TEAM, "H", ON, tmp_path / "repos", tmp_path / "reports", client=client)
    assert KEY not in str(info.value)


def test_refuses_to_run_when_off(tmp_path):
    make_repo(tmp_path)
    off = Settings(enabled=False, api_key=KEY, model=DEFAULT_MODEL, max_input_chars=1000)
    with pytest.raises(AiReviewError, match="apagada"):
        write_ai_review(TEAM, "H", off, tmp_path / "repos", tmp_path / "reports")


def test_documents_from_equipos_yml_are_not_sent():
    team = Team(id="t", nombre="n", proyecto="p", objetivo="o", repo="r",
                presentacion="https://slides.example/x", prompts_ia="C:/entregas/p.pdf")
    review = ai_review.complete_review(answer(), team, "m", NOW)
    assert review["documents"]["presentation"]["status"] == "url_only"
    assert review["documents"]["prompts_pdf"]["status"] == "unreadable"


# --------------------------------------------------------------------------- CLI


def test_cli_is_off_by_default(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ACR_AI_REVIEW", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    assert main(["ai-review"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "ACR_AI_REVIEW=true" in err and KEY not in err
