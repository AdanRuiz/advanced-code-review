"""Static checks of ``action.yml`` (PB-64, SPEC.md "GitHub Action para otros repositorios: Reglas")."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ACTION = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
WORKFLOWS = [
    yaml.safe_load(path.read_text(encoding="utf-8"))
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
]
STEPS = ACTION["runs"]["steps"]
RUN_STEP = next(step for step in STEPS if step.get("id") == "acr")


def all_steps():
    yield from STEPS
    for workflow in WORKFLOWS:
        for job in workflow["jobs"].values():
            yield from job["steps"]


def test_composite_action():
    assert ACTION["runs"]["using"] == "composite"


def test_ai_review_is_off_by_default_and_uses_v41_flash():
    inputs = ACTION["inputs"]
    assert inputs["ai-review"]["default"] == "false"
    assert inputs["openrouter-api-key"]["default"] == ""
    assert inputs["ai-model"]["default"] == "deepseek/deepseek-v4.1-flash-1017"
    assert inputs["fail-on"]["default"] == "none"


def test_every_input_reaches_acr_ci_through_the_environment():
    values = " ".join(str(value) for value in RUN_STEP["env"].values())
    for name in ACTION["inputs"]:
        if name in ("retention-days", "artifact-name"):
            continue  # used by the upload step
        assert f"inputs.{name}" in values, name


def test_inputs_are_never_interpolated_into_scripts():
    """``${{ inputs.* }}`` inside ``run:`` would let an input inject shell commands."""
    for step in all_steps():
        assert "${{" not in step.get("run", ""), step.get("name")


def test_third_party_actions_are_pinned_by_sha():
    for step in all_steps():
        uses = step.get("uses")
        if uses and not uses.startswith("./"):
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uses), uses


def test_python_never_imports_from_the_validated_repository():
    assert RUN_STEP["working-directory"] == "${{ github.action_path }}"
    assert "python -P -m acr ci" in RUN_STEP["run"]
    assert "--config" not in RUN_STEP["run"]


def test_scc_download_is_checked():
    install = next(step for step in STEPS if "scc" in step.get("name", ""))
    assert "sha256sum --check" in install["run"]


def test_workflows_only_read_the_repository():
    for workflow in WORKFLOWS:
        assert workflow["permissions"] == {"contents": "read"}
