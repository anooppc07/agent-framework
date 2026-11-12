"""Prompt-path deployment boundary (plan decision 8, user choice "Option A"): the prompt workflow
template must be manual-dispatch only, and creation must stage it, not install it into the active
workflows folder. Real files, parsed as YAML. Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW = ROOT / "prompt" / "assets" / "agent" / "workflows" / "deploy-agent.yml"
FLOW = ROOT / "prompt" / "FLOW.md"
README = ROOT / "prompt" / "assets" / "agent" / "README.md"
AUTOMATIC = ("push", "pull_request", "pull_request_target", "schedule", "workflow_run", "release", "repository_dispatch")


def rendered():
    """Replace the author-time {{TOKENS}} so the template parses; ${{ ... }} expressions are untouched."""
    text = WORKFLOW.read_text(encoding="utf-8")
    return re.sub(r"(?<!\$)\{\{[A-Z][A-Z0-9_]*\}\}", "sample", text)


def triggers():
    doc = yaml.safe_load(rendered())
    on = doc.get("on", doc.get(True))  # YAML 1.1 reads the bare key `on` as boolean True
    return on if isinstance(on, dict) else {k: None for k in ([on] if isinstance(on, str) else on)}


def test_workflow_parses_and_has_the_three_jobs():
    jobs = yaml.safe_load(rendered())["jobs"]
    assert list(jobs) == ["deploy-dev", "eval", "deploy-demo"]


def test_only_manual_dispatch_triggers_the_deployment():
    t = triggers()
    assert "workflow_dispatch" in t
    assert set(t) == {"workflow_dispatch"}, f"unexpected triggers: {sorted(set(t) - {'workflow_dispatch'})}"


@pytest.mark.parametrize("name", AUTOMATIC)
def test_no_automatic_trigger_is_declared(name):
    assert name not in triggers()


def test_comments_do_not_hide_a_trigger_and_explain_the_manual_entrypoint():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?mi)^\s*#.*(manual|dispatch)", text), "template should say why it is manual-only"


def test_oidc_permissions_and_environments_are_unchanged():
    doc = yaml.safe_load(rendered())
    assert doc["permissions"] == {"id-token": "write", "contents": "read"}
    assert [doc["jobs"][j]["environment"] for j in doc["jobs"]] == ["dev", "dev", "demo"]
    assert doc["jobs"]["deploy-demo"]["needs"] == "eval" and doc["jobs"]["eval"]["needs"] == "deploy-dev"


def test_flow_stages_the_workflow_and_does_not_install_it_into_the_active_folder():
    flow = FLOW.read_text(encoding="utf-8")
    step = flow[flow.index("5. **Scaffold the pipeline**"):flow.index("6. **Write decision records.**")]
    assert "workflow_dispatch" in step and "manual" in step.lower()
    assert "do not" in step.lower() and ".github/workflows" in step
    assert not re.search(r"(?i)\bcopy\b[^.]*to the repo's `\.github/workflows/`", step), \
        "step 5 still tells the creator to install into the active workflows folder"
    assert "workflows/deploy-agent.yml" in step or "workflows/" in step


def test_agent_readme_tells_the_user_how_to_install_and_run_the_staged_workflow():
    text = README.read_text(encoding="utf-8")
    assert "workflow_dispatch" in text or "Run workflow" in text
    assert ".github/workflows" in text and "deploy skill" in text.lower()
