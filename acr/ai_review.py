"""Automated AI review through OpenRouter (PB-63, SPEC.md "Revisión con IA vía OpenRouter").

``acr ai-review`` writes the same ``reports/<id>/review.json`` as the assisted review with
Claude Code, from a single call to a model on OpenRouter: no tools, no web browsing. It is a
draft (``status: "draft"``, ``tool: "openrouter"``, ``generated_by``: the model) that a person
validates, and it is used by the report only if it passes the ``acr review-validate`` rules.

It is **off by default**: it only runs when ``ACR_AI_REVIEW`` is ``true``. Having a key is not
enough. The key comes from ``OPENROUTER_API_KEY`` and the model from ``ACR_AI_MODEL``.

What is sent: the file tree, the README, the dependency manifests and the most relevant code
files up to ``ACR_AI_MAX_INPUT_CHARS``, plus a summary of ``scc.json``, ``git-signals.json``,
``scanoss.json`` and the Betterleaks findings (type, file and line only). Files where Betterleaks
found a secret, ``.env``-like files, keys and certificates are never sent, and anything that
looks like a credential in the other files is replaced by ``[REDACTED]``. The repository content
is wrapped in ``<repository_data>`` and marked as data, not instructions. The request asks
OpenRouter for providers that do not store the data (``provider.data_collection: "deny"``).
The team's code is never executed and the key never appears in the output or in errors.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from string import Template
from typing import Any

import httpx

from acr.config import Team, is_valid_team_id
from acr.report import secret_findings
from acr.review import (
    AI_CHECKLIST,
    EXTERNAL_CODE_ORIGINS,
    JUDGE_TOPICS,
    OFFICIAL_CHECKLIST,
    OWASP_LLM,
    PACKAGE_DIR,
    REVIEW_FILE,
    REVIEW_SCHEMA_VERSION,
    SECRET_PATTERNS,
    validate_review,
)
from acr.scanoss import dependency_dir

DEFAULT_REPORTS_DIR = Path("reports")
PROMPT_FILE = PACKAGE_DIR / "prompts" / "ai-review.md"

ENV_ENABLED = "ACR_AI_REVIEW"
ENV_KEY = "OPENROUTER_API_KEY"
ENV_MODEL = "ACR_AI_MODEL"
ENV_MAX_CHARS = "ACR_AI_MAX_INPUT_CHARS"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_MAX_INPUT_CHARS = 200_000
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT_SECONDS = 5 * 60
TRUE_VALUES = frozenset({"true", "1", "yes"})

# Per file: bigger files are cut so one file cannot take the whole budget.
MAX_FILE_CHARS = 20_000
# Files bigger than this on disk are skipped without reading them (minified bundles, data).
MAX_FILE_BYTES = 512 * 1024
MAX_TREE_ENTRIES = 2_000
REDACTED = "[REDACTED]"

README_RE = re.compile(r"^readme(\.[a-z0-9]+)?$", re.IGNORECASE)
MANIFESTS = frozenset(
    {
        "package.json", "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "pipfile",
        "go.mod", "cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "gemfile",
        "composer.json", "pubspec.yaml", "dockerfile", "docker-compose.yml", "compose.yaml",
    }
)
CODE_SUFFIXES = frozenset(
    {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".java", ".kt",
        ".go", ".rs", ".rb", ".php", ".cs", ".c", ".h", ".cpp", ".hpp", ".swift", ".dart",
        ".scala", ".sql", ".html", ".css", ".scss", ".sh", ".md", ".yml", ".yaml", ".toml",
    }
)
LOCK_FILES = frozenset(
    {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock", "cargo.lock",
     "composer.lock", "gemfile.lock", "go.sum"}
)
# Never sent, whatever their content: environment files, keys, certificates and credentials.
SENSITIVE_RE = re.compile(
    r"(^\.env(\..*)?$|\.(pem|key|p12|pfx|jks|keystore|crt|cer|der|kdbx)$|^id_(rsa|dsa|ecdsa|ed25519)"
    r"|^\.npmrc$|^\.pypirc$|^\.netrc$|^credentials(\..*)?$|^secrets?(\..*)?$)",
    re.IGNORECASE,
)


class AiReviewError(Exception):
    """The AI review could not be written; the message never contains the key."""


@dataclass(frozen=True)
class Settings:
    enabled: bool
    api_key: str | None
    model: str
    max_input_chars: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        enabled = env.get(ENV_ENABLED, "").strip().lower() in TRUE_VALUES
        key = env.get(ENV_KEY, "").strip() or None
        model = env.get(ENV_MODEL, "").strip() or DEFAULT_MODEL
        raw_max = env.get(ENV_MAX_CHARS, "").strip()
        try:
            max_chars = int(raw_max) if raw_max else DEFAULT_MAX_INPUT_CHARS
        except ValueError as exc:
            raise AiReviewError(f"{ENV_MAX_CHARS} debe ser un entero positivo ({raw_max!r})") from exc
        if max_chars <= 0:
            raise AiReviewError(f"{ENV_MAX_CHARS} debe ser un entero positivo ({raw_max!r})")
        return cls(enabled=enabled, api_key=key, model=model, max_input_chars=max_chars)

    def skip_reason(self) -> str | None:
        """Why the review does not run, or ``None`` if it can run."""
        if not self.enabled:
            return f"revisión con IA apagada ({ENV_ENABLED} no es true)"
        if not self.api_key:
            return f"revisión con IA activada pero sin key ({ENV_KEY} vacía)"
        return None


@dataclass(frozen=True)
class AiReviewResult:
    path: Path
    model: str
    files_sent: int
    chars_sent: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cost: float | None


# --------------------------------------------------------------------------- what is sent


def redact(text: str) -> str:
    for _kind, pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def secret_paths(reports_dir: Path, team_id: str) -> set[str]:
    """Files where Betterleaks found a secret (``megalinter.sarif``); they are never sent."""
    path = reports_dir / team_id / "megalinter.sarif"
    try:
        sarif = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    if not isinstance(sarif, dict):
        return set()
    return {f.path for f in secret_findings(sarif)}


def _is_excluded(rel: PurePosixPath, secrets: set[str]) -> bool:
    if ".git" in rel.parts or dependency_dir(rel.as_posix()):
        return True
    if rel.name.lower() in LOCK_FILES or SENSITIVE_RE.search(rel.name):
        return True
    return rel.as_posix() in secrets


def _priority(rel: PurePosixPath) -> tuple[int, int, str]:
    name = rel.name.lower()
    if README_RE.match(rel.name):
        group = 0
    elif name in MANIFESTS:
        group = 1
    else:
        group = 2
    return (group, len(rel.parts), rel.as_posix())


def list_files(repo: Path, secrets: set[str]) -> list[PurePosixPath]:
    """Regular files of ``repo`` that may be sent, without following symbolic links."""
    found = []
    for dirpath, dirnames, filenames in os.walk(repo, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d != ".git" and not (base / d).is_symlink())
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = PurePosixPath(path.relative_to(repo).as_posix())
            if not _is_excluded(rel, secrets):
                found.append(rel)
    return found


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _numbered(text: str) -> str:
    return "\n".join(f"{n}| {line}" for n, line in enumerate(text.splitlines(), start=1))


def collect_files(repo: Path, secrets: set[str], max_chars: int) -> tuple[list[str], list[tuple[str, str]]]:
    """The file tree and the ``(path, numbered text)`` of the files that fit in ``max_chars``."""
    files = list_files(repo, secrets)
    tree = [rel.as_posix() for rel in files[:MAX_TREE_ENTRIES]]
    chosen: list[tuple[str, str]] = []
    used = 0
    for rel in sorted(files, key=_priority):
        name = rel.name.lower()
        if not (README_RE.match(rel.name) or name in MANIFESTS or rel.suffix.lower() in CODE_SUFFIXES):
            continue
        text = _read_text(repo / Path(*rel.parts))
        if text is None or not text.strip():
            continue
        text = redact(text)
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n[... truncated ...]"
        block = _numbered(text)
        if used + len(block) > max_chars:
            continue
        chosen.append((rel.as_posix(), block))
        used += len(block)
    return tree, chosen


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def tool_summary(reports_dir: Path, team_id: str) -> dict[str, Any]:
    """What the static tools found, without code or secret values."""
    folder = reports_dir / team_id
    summary: dict[str, Any] = {}
    scc = _load(folder / "scc.json")
    if scc:
        summary["scc"] = {"totals": scc.get("totals"), "languages": (scc.get("languages") or [])[:10]}
    signals = _load(folder / "git-signals.json")
    if signals:
        keys = ("has_git", "commit_count", "single_commit", "first_commit", "last_commit",
                "largest_commit_percent", "author_count", "single_author", "fork", "outside_event")
        picked = {key: signals.get(key) for key in keys}
        outside = signals.get("outside_event")
        if isinstance(outside, dict):
            picked["outside_event"] = {k: outside.get(k) for k in ("before_start", "after_end")}
        summary["git_signals"] = picked
    scanoss = _load(folder / "scanoss.json")
    if scanoss:
        matches = scanoss.get("matches") if isinstance(scanoss.get("matches"), list) else []
        keys = ("path", "match", "matched_percent", "component", "version", "licenses", "url")
        summary["scanoss"] = {
            "totals": scanoss.get("totals"),
            "matches": [{k: m.get(k) for k in keys} for m in matches[:30] if isinstance(m, dict)],
        }
    secrets = sorted(secret_paths(reports_dir, team_id))
    summary["betterleaks_files_with_secrets"] = secrets
    return summary


def _prompt(team: Team, event_name: str) -> str:
    def lines(items: Mapping[str, str]) -> str:
        return "\n".join(f"  - `{key}`: {value}" for key, value in items.items())

    def one_line(text: str) -> str:
        return " ".join(text.split())

    return Template(PROMPT_FILE.read_text(encoding="utf-8")).substitute(
        team_id=team.id,
        event_name=one_line(event_name),
        team_project=one_line(team.proyecto),
        team_goal=one_line(team.objetivo),
        official_checklist=lines(OFFICIAL_CHECKLIST),
        external_origins=lines(EXTERNAL_CODE_ORIGINS),
        judge_topics=lines(JUDGE_TOPICS),
        ai_checklist=lines(AI_CHECKLIST),
        owasp_checklist=lines(OWASP_LLM),
    )


def build_messages(
    team: Team, event_name: str, tree: list[str], files: list[tuple[str, str]], summary: dict[str, Any]
) -> list[dict[str, str]]:
    parts = [
        "<repository_data>",
        "## File tree",
        *tree,
        "",
        "## Static analysis summary (JSON)",
        json.dumps(summary, ensure_ascii=False, indent=1),
        "",
        "## Files (line numbers on the left of |)",
    ]
    for path, block in files:
        parts += [f"=== FILE: {path} ===", block, f"=== END FILE: {path} ==="]
    parts.append("</repository_data>")
    return [
        {"role": "system", "content": _prompt(team, event_name)},
        {"role": "user", "content": "\n".join(parts)},
    ]


# --------------------------------------------------------------------------- OpenRouter


def _scrub(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def call_openrouter(
    client: httpx.Client, settings: Settings, messages: list[dict[str, str]]
) -> tuple[str, dict[str, Any]]:
    """One chat completion; returns the content and the ``usage`` block."""
    key = settings.api_key or ""
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "provider": {"data_collection": "deny"},
        "usage": {"include": True},
    }
    headers = {"Authorization": f"Bearer {key}", "X-Title": "Advanced Code Review"}
    try:
        response = client.post(OPENROUTER_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise AiReviewError(_scrub(f"no se pudo llamar a OpenRouter: {type(exc).__name__}", key)) from None
    if response.status_code in (400, 404) and "model" in response.text.lower():
        code = response.status_code
        raise AiReviewError(f"OpenRouter no tiene el modelo {settings.model!r} (código {code})")
    if response.status_code == 401:
        raise AiReviewError("OpenRouter rechazó la key (código 401)")
    if response.status_code == 402:
        raise AiReviewError("la cuenta de OpenRouter no tiene saldo (código 402)")
    if response.status_code >= 400:
        raise AiReviewError(f"OpenRouter respondió con código {response.status_code}")
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise AiReviewError("OpenRouter devolvió una respuesta sin contenido") from None
    if not isinstance(content, str):
        raise AiReviewError("OpenRouter devolvió una respuesta sin contenido")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return content, usage


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_answer(content: str) -> dict[str, Any] | None:
    match = _FENCE_RE.match(content)
    text = match.group(1) if match else content
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _document(value: str | None, url_allowed: bool, label: str) -> dict[str, str]:
    if not value:
        return {"status": "not_provided", "notes": f"No {label} was provided."}
    if url_allowed and value.lower().startswith(("http://", "https://")):
        return {"status": "url_only", "notes": f"Only a URL to the {label}; it is not sent to the AI review."}
    return {"status": "unreadable", "notes": f"The {label} is not sent to the AI review; check it manually."}


def complete_review(
    answer: dict[str, Any], team: Team, model: str, now: datetime | None = None
) -> dict[str, Any]:
    """The fixed fields are set here, never taken from the model."""
    review = {key: value for key, value in answer.items() if key not in {"documents", "generated_by"}}
    stamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    for finding in review.get("findings") or []:
        if isinstance(finding, dict):
            finding["source"] = "openrouter"
    review.update(
        schema_version=REVIEW_SCHEMA_VERSION,
        team_id=team.id,
        tool="openrouter",
        generated_by=model,
        generated_at=stamp,
        status="draft",
        documents={
            "prompts_pdf": _document(team.prompts_ia, url_allowed=False, label="AI prompts PDF"),
            "presentation": _document(team.presentacion, url_allowed=True, label="presentation"),
        },
    )
    return review


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _add(total: int | float | None, value: Any) -> int | float | None:
    value = _number(value)
    if value is None:
        return total
    return value if total is None else total + value


def write_ai_review(
    team: Team,
    event_name: str,
    settings: Settings,
    repos_dir: Path = Path("repos"),
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    *,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> AiReviewResult:
    """Review ``repos_dir/<id>`` and write ``reports_dir/<id>/review.json``.

    The answer is validated like ``acr review-validate``; if it is invalid the model gets one
    retry with the errors. A previous ``review.json`` is only replaced by a valid one.
    """
    if not is_valid_team_id(team.id):
        raise AiReviewError(f"id inválido ({team.id!r})")
    reason = settings.skip_reason()
    if reason:
        raise AiReviewError(reason)
    repo = repos_dir / team.id
    if not repo.is_dir():
        raise AiReviewError(f"no existe {repo.as_posix()}; corre 'acr fetch {team.id}'")

    tree, files = collect_files(repo, secret_paths(reports_dir, team.id), settings.max_input_chars)
    messages = build_messages(team, event_name, tree, files, tool_summary(reports_dir, team.id))
    owns_client = client is None
    client = client or httpx.Client()
    prompt_tokens = completion_tokens = cost = None
    try:
        review: dict[str, Any] | None = None
        errors: list[str] = []
        for _attempt in range(2):
            content, usage = call_openrouter(client, settings, messages)
            prompt_tokens = _add(prompt_tokens, usage.get("prompt_tokens"))
            completion_tokens = _add(completion_tokens, usage.get("completion_tokens"))
            cost = _add(cost, usage.get("cost"))
            answer = parse_answer(content)
            if answer is None:
                errors = ["la respuesta no es un objeto JSON"]
            else:
                review = complete_review(answer, team, settings.model, now)
                errors = validate_review(review, team.id)
                if not errors:
                    break
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": "Your JSON is invalid. Fix these errors and answer again with the "
                    "whole JSON object only:\n- " + "\n- ".join(errors[:30]),
                },
            ]
        if errors or review is None:
            raise AiReviewError(f"la respuesta del modelo no es válida ({len(errors)} error(es))")
    finally:
        if owns_client:
            client.close()

    out_dir = reports_dir / team.id
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / REVIEW_FILE
    staging = out_dir / f".{REVIEW_FILE}.tmp"
    staging.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staging.replace(target)
    return AiReviewResult(
        path=target,
        model=settings.model,
        files_sent=len(files),
        chars_sent=sum(len(block) for _path, block in files),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )


def describe(result: AiReviewResult) -> str:
    """One line with what was sent and what it cost."""
    tokens = ""
    if result.prompt_tokens is not None or result.completion_tokens is not None:
        tokens = (
            f", {result.prompt_tokens or 0} tokens de entrada y {result.completion_tokens or 0} de salida"
        )
    cost = f", costo USD {result.cost:.4f}" if result.cost is not None else ""
    return f"{result.model}: {result.files_sent} archivo(s), {result.chars_sent} caracteres{tokens}{cost}"
