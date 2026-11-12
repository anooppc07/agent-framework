"""Pipeline rendering (plan Task 7, approved cases 1-4 plus rendering and end-to-end rules).

Real `agent_spec`, `pipeline_assets` and `hosted_pipeline` code on real files, workflows parsed as YAML.
The only fake is `FakeAdapter` (the external Foundry/CLI boundary), used for the rendered-project
end-to-end test. Nothing here installs a workflow, contacts GitHub or Azure.
Run from overlay/ with PYTHONPATH unset:  python -m pytest tests -q
"""

import ast
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.dont_write_bytecode = True
OVERLAY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OVERLAY))
sys.path.insert(0, str(OVERLAY.parent / "hosted" / "tests"))

import agent_spec as A  # noqa: E402
import pipeline_assets as P  # noqa: E402
import pipeline_fakes as F  # noqa: E402

hp = F.hp
EXPECTED = {"agent-ci.yml", "agent-deploy.yml", "hosted_pipeline.py", "release.json", "README.md"}
SOURCE = P.PIPELINE_DIR
REPO = "github.com/DeepuMM/agent-deployment"
SHA = "2220a3c2e3964728aef4444048944db0ebf41ecc"
AUTOMATIC = ("push", "pull_request", "pull_request_target", "schedule", "workflow_run", "release",
             "repository_dispatch", "deployment", "check_suite")
PLACEHOLDER = re.compile(r"(?<!\$)\{\{[A-Z][A-Z0-9_]*\}\}")
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def spec_dict(**over):
    d = {
        "name": "card-helper", "description": "Dispute intake", "owner": "payments-team",
        "classification": "internal", "agent_kind": "hosted", "framework": "maf",
        "pattern": "single-agent", "pattern_rationale": "one agent is enough", "model": "gpt-5.4",
        "hosted_session_manager": "enabled", "tools": [{"name": "lookup", "auth": "none"}],
        "evals_enabled": True, "pipeline_enabled": True,
        "evaluation": {"judge_model": "judge", "min_pass_rate": 0.8, "evaluators": ["relevance", "task_adherence"],
                       "dataset_status": "approved", "cases": [{"query": f"q{i}"} for i in range(3)]},
    }
    d.update(over)
    return A.parse_spec(d)


def router_dict(**over):
    d = dict(
        name="card-router", pattern="router-specialists", tools=[],
        specialists=[{"name": "billing", "instructions": "x", "examples": ["b1"]},
                     {"name": "fraud", "instructions": "y", "examples": ["f1"]}],
        evaluation={"judge_model": "judge", "min_pass_rate": 0.8, "evaluators": ["relevance"],
                    "dataset_status": "approved",
                    "cases": [{"query": "b1", "expected_route": "billing"},
                              {"query": "f1", "expected_route": "fraud"},
                              {"query": "x1", "expected_route": "refusal"}]},
    )
    d.update(over)
    return spec_dict(**d)


@pytest.fixture
def out(tmp_path):
    return tmp_path / "agent"


@pytest.fixture
def rendered(out):
    P.render_pipeline(spec_dict(), out, agent_dir="src/agents/card-helper")
    return {p.name: p.read_text(encoding="utf-8") for p in (out / "pipeline").iterdir()}


def workflow(rendered, name):
    return yaml.safe_load(rendered[name])


def triggers(doc):
    on = doc.get("on", doc.get(True))  # PyYAML reads the bare key `on` as boolean True
    return on if isinstance(on, dict) else {k: None for k in ([on] if isinstance(on, str) else on)}


def digest_tree(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


# --- what gets rendered ---------------------------------------------------------------------


def test_the_selected_pipeline_files_are_rendered_under_the_agent(out):
    written = P.render_pipeline(spec_dict(), out)
    assert {p.name for p in written} == EXPECTED
    assert {p.name for p in (out / "pipeline").iterdir()} == EXPECTED
    assert all(p.parent == out / "pipeline" for p in written)


def test_pipeline_disabled_renders_nothing_and_creates_nothing(tmp_path):
    spec = spec_dict(pipeline_enabled=False)
    assert P.pipeline_files(spec) == {}
    target = tmp_path / "agent"
    assert P.render_pipeline(spec, target) == []
    assert not target.exists()


def test_evals_disabled_cannot_produce_a_pipeline(tmp_path):
    spec = dataclasses.replace(spec_dict(), evals_enabled=False)  # parse_spec already refuses this combination
    with pytest.raises(P.PipelineError):
        P.pipeline_files(spec)
    assert not (tmp_path / "agent").exists()


@pytest.mark.parametrize("over", [
    {"agent_kind": "prompt", "framework": "not-applicable", "hosted_session_manager": "not-applicable"},
    {"framework": "legacy-langgraph"},
])
def test_only_maf_hosted_agents_get_the_hosted_pipeline(over):
    spec = dataclasses.replace(spec_dict(), **over)
    with pytest.raises(P.PipelineError):
        P.pipeline_files(spec)


def test_workflows_are_never_written_into_a_github_workflows_directory(tmp_path):
    target = tmp_path / "repo" / ".github" / "workflows" / "agent"
    with pytest.raises(P.PipelineError):
        P.render_pipeline(spec_dict(), target)
    assert not (tmp_path / "repo").exists()


def test_rendering_into_a_normal_tree_creates_no_github_directory(out):
    P.render_pipeline(spec_dict(), out)
    assert not [p for p in out.rglob("*") if p.name in (".github", "workflows")]


def test_existing_pipeline_output_is_refused_before_any_write(out):
    (out / "pipeline").mkdir(parents=True)
    (out / "pipeline" / "agent-deploy.yml").write_text("mine\n", encoding="utf-8")
    with pytest.raises(P.PipelineError):
        P.render_pipeline(spec_dict(), out)
    assert [p.name for p in (out / "pipeline").iterdir()] == ["agent-deploy.yml"]
    assert (out / "pipeline" / "agent-deploy.yml").read_text(encoding="utf-8") == "mine\n"


def test_missing_source_fails_before_any_output_and_leaves_sources_unchanged(tmp_path, out):
    src = tmp_path / "src"
    shutil.copytree(SOURCE, src, ignore=shutil.ignore_patterns("__pycache__"))
    (src / "agent-deploy.yml").unlink()
    before = digest_tree(src)
    with pytest.raises(P.PipelineError):
        P.render_pipeline(spec_dict(), out, source_dir=src)
    assert not out.exists() and digest_tree(src) == before


def test_caches_and_stray_files_in_the_source_folder_are_not_copied(tmp_path, out):
    src = tmp_path / "src"
    shutil.copytree(SOURCE, src, ignore=shutil.ignore_patterns("__pycache__"))
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "hosted_pipeline.cpython-313.pyc").write_bytes(b"\x00")
    (src / "notes.txt").write_text("stray\n", encoding="utf-8")
    P.render_pipeline(spec_dict(), out, source_dir=src)
    assert {p.name for p in (out / "pipeline").iterdir()} == EXPECTED


def test_rendering_uses_the_given_source_folder(tmp_path, out):
    src = tmp_path / "src"
    shutil.copytree(SOURCE, src, ignore=shutil.ignore_patterns("__pycache__"))
    marker = "# marker-from-the-custom-source-folder" + chr(10)
    (src / "agent-ci.yml").write_text(marker + (src / "agent-ci.yml").read_text(encoding="utf-8"), encoding="utf-8")
    P.render_pipeline(spec_dict(), out, source_dir=src)
    assert (out / "pipeline" / "agent-ci.yml").read_text(encoding="utf-8").startswith(marker)


def test_source_templates_are_not_modified_by_rendering(out):
    before = digest_tree(SOURCE)
    P.render_pipeline(spec_dict(), out)
    assert digest_tree(SOURCE) == before


def test_no_unresolved_placeholders_survive(rendered):
    for name, text in rendered.items():
        assert not PLACEHOLDER.findall(text), f"{name}: {PLACEHOLDER.findall(text)}"


@pytest.mark.parametrize("bad", ["", "../x", "/abs", "a b", "a\\b", "a;b", "$(x)", "a/../b", "-rf", "a/./b", "C:/x"])
def test_unsafe_agent_directories_are_rejected(bad):
    with pytest.raises(P.PipelineError):
        P.pipeline_files(spec_dict(), agent_dir=bad)


@pytest.mark.parametrize("good", [".", "src/agents/card-helper", "agents/x_y.z"])
def test_safe_agent_directories_render_into_the_workflows(good):
    files = P.pipeline_files(spec_dict(), agent_dir=good)
    assert yaml.safe_load(files["agent-deploy.yml"]) and yaml.safe_load(files["agent-ci.yml"])


def test_the_default_agent_directory_follows_the_skill_convention():
    files = P.pipeline_files(spec_dict())
    assert "src/agents/card-helper" in files["agent-ci.yml"]


# --- C1: CI workflow --------------------------------------------------------------------------


def test_ci_triggers_only_on_pull_request(rendered):
    assert set(triggers(workflow(rendered, "agent-ci.yml"))) == {"pull_request"}


def test_ci_permissions_are_exactly_contents_read(rendered):
    doc = workflow(rendered, "agent-ci.yml")
    assert doc["permissions"] == {"contents": "read"}
    assert all("permissions" not in job for job in doc["jobs"].values())


def test_ci_has_no_azure_access_of_any_kind(rendered):
    text = rendered["agent-ci.yml"]
    doc = workflow(rendered, "agent-ci.yml")
    uses = [s.get("uses", "") for job in doc["jobs"].values() for s in job["steps"]]
    assert not any(u.startswith("azure/") for u in uses)
    for needle in ("azure/login", "id-token", "secrets.", "vars.", "environment:", "AZURE_"):
        assert needle not in text, needle


def test_ci_runs_the_session_preflight_and_the_pipeline_validation(rendered):
    steps = workflow(rendered, "agent-ci.yml")["jobs"]["validate"]["steps"]
    runs = [s["run"] for s in steps if "run" in s]
    assert any("preflight.py" in r for r in runs) and any("hosted_pipeline.py validate" in r for r in runs)


def test_ci_is_scoped_to_the_agent_directory(rendered):
    pr = triggers(workflow(rendered, "agent-ci.yml")).get("pull_request") or {}
    assert pr.get("paths") == ["src/agents/card-helper/**"]


# --- C2: deploy workflow triggers -------------------------------------------------------------


def test_deploy_is_workflow_dispatch_only(rendered):
    assert set(triggers(workflow(rendered, "agent-deploy.yml"))) == {"workflow_dispatch"}


@pytest.mark.parametrize("name", AUTOMATIC)
def test_deploy_declares_no_automatic_trigger(rendered, name):
    assert name not in triggers(workflow(rendered, "agent-deploy.yml"))


def test_deploy_requires_an_explicit_agent_and_environment_input(rendered):
    inputs = (triggers(workflow(rendered, "agent-deploy.yml")).get("workflow_dispatch") or {}).get("inputs") or {}
    assert set(inputs) == {"agent", "environment"}
    assert all(i.get("required") is True and i.get("type") == "string" for i in inputs.values())


def test_comments_explain_the_manual_entrypoint_and_the_blocked_status(rendered):
    text = rendered["agent-deploy.yml"]
    assert re.search(r"(?mi)^\s*#.*(manual|dispatch)", text)
    assert re.search(r"(?m)^\s*#.*BLOCKED", text)


# --- C3: OIDC, environment, per-target concurrency --------------------------------------------


def test_deploy_permissions_are_oidc_and_read_only(rendered):
    doc = workflow(rendered, "agent-deploy.yml")
    assert doc["permissions"] == {"id-token": "write", "contents": "read"}
    assert all("permissions" not in job for job in doc["jobs"].values())


def test_deploy_job_uses_the_dispatched_github_environment(rendered):
    job = next(iter(workflow(rendered, "agent-deploy.yml")["jobs"].values()))
    assert job.get("environment") == "${{ inputs.environment }}"


def test_deploy_concurrency_is_per_agent_and_environment_and_never_cancels(rendered):
    doc = workflow(rendered, "agent-deploy.yml")
    conc = doc.get("concurrency") or next(iter(doc["jobs"].values())).get("concurrency") or {}
    assert "inputs.agent" in conc.get("group", "") and "inputs.environment" in conc.get("group", "")
    assert conc.get("cancel-in-progress") is False


def test_the_check_and_release_steps_always_run(rendered):
    steps = next(iter(workflow(rendered, "agent-deploy.yml")["jobs"].values()))["steps"]
    gated = [s for s in steps if "hosted_pipeline.py" in s.get("run", "") or s.get("uses", "").startswith("azure/login")]
    assert len(gated) == 3
    assert not [s for s in gated if "if" in s or s.get("continue-on-error")], "the gate steps must be unconditional"


def test_preflight_check_precedes_the_azure_login_which_precedes_the_release(rendered):
    steps = next(iter(workflow(rendered, "agent-deploy.yml")["jobs"].values()))["steps"]
    kinds = []
    for s in steps:
        if s.get("uses", "").startswith("azure/login"):
            kinds.append("login")
        elif "hosted_pipeline.py check" in s.get("run", ""):
            kinds.append("check")
        elif "hosted_pipeline.py release" in s.get("run", ""):
            kinds.append("release")
    assert kinds == ["check", "login", "release"]


def test_dispatch_inputs_never_reach_a_shell_command_inline(rendered):
    for job in workflow(rendered, "agent-deploy.yml")["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step["run"]


def test_deploy_reads_every_environment_value_from_vars_not_literals(rendered):
    doc = workflow(rendered, "agent-deploy.yml")
    job = next(iter(doc["jobs"].values()))
    logins = [s for s in job["steps"] if s.get("uses", "").startswith("azure/login")]
    assert len(logins) == 1
    login = logins[0]["with"]
    assert login == {"client-id": "${{ vars.AZURE_CLIENT_ID }}", "tenant-id": "${{ vars.AZURE_TENANT_ID }}",
                     "subscription-id": "${{ vars.AZURE_SUBSCRIPTION_ID }}"}
    for key in ("FOUNDRY_PROJECT_ENDPOINT", "AZURE_AI_MODEL_DEPLOYMENT_NAME"):
        assert job["env"][key] == "${{ vars." + key + " }}"
    assert "secrets." not in rendered["agent-deploy.yml"]


def test_failed_release_still_uploads_the_report(rendered):
    job = next(iter(workflow(rendered, "agent-deploy.yml")["jobs"].values()))
    upload = [s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact")]
    assert upload and upload[-1].get("if") == "always()"


# --- C4: no prompt-agent deployment, no APIM, no hardcoded identifiers ------------------------


def test_no_prompt_agent_definition_apim_or_gateway_code_in_any_generated_file(rendered):
    for name, text in rendered.items():
        low = text.lower()
        assert "promptagentdefinition" not in low, name
        for needle in ("azure-api.net", "management.azure.com", "namedvalues", "ocp-apim", "developer portal",
                       "create_version", "azure.ai.projects"):
            assert needle not in low, f"{name}: {needle}"
        if name != "README.md":
            assert "apim" not in low, name


def test_no_hardcoded_subscription_resource_or_endpoint_values(rendered):
    for name, text in rendered.items():
        assert not GUID.search(text), name
        for needle in ("/subscriptions/", "resourcegroups/", ".services.ai.azure.com", ".openai.azure.com",
                       "cognitiveservices.azure.com"):
            assert needle not in text.lower(), f"{name}: {needle}"
    for name in ("agent-ci.yml", "agent-deploy.yml"):
        assert "http://" not in rendered[name] and "https://" not in rendered[name]


def test_the_helper_imports_only_the_standard_library(rendered):
    tree = ast.parse(rendered["hosted_pipeline.py"])
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add(node.module.split(".")[0])
    assert roots <= set(sys.stdlib_module_names), sorted(roots - set(sys.stdlib_module_names))


def test_rendered_helper_is_a_verbatim_copy_of_the_tested_source(rendered):
    assert rendered["hosted_pipeline.py"] == (SOURCE / "hosted_pipeline.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["agent-ci.yml", "agent-deploy.yml", "hosted_pipeline.py", "README.md"])
def test_every_file_adapted_from_the_reference_carries_the_attribution(rendered, name):
    head = rendered[name][:1500]
    assert REPO in head and SHA in head
    assert "license" in head.lower()


# --- release.json: the rendered release configuration -----------------------------------------


class _Cfg(dict):
    """Missing keys read as None so a wrong config fails the assertion, not with a KeyError."""

    def __missing__(self, key):
        return None


def release(rendered):
    return _Cfg(json.loads(rendered["release.json"]))


def test_release_config_carries_the_confirmed_evaluation_settings(rendered):
    cfg = release(rendered)
    assert cfg["schema"] == 1 and cfg["agent"] == "card-helper" and cfg["router"] is False
    assert cfg["evaluators"] == ["relevance", "task_adherence"] and cfg["min_pass_rate"] == 0.8
    assert cfg["judge_model"] == "judge" and cfg["case_count"] == 3 and cfg["dataset_status"] == "approved"
    assert cfg["tools"] == [{"name": "lookup", "auth": "none", "requires_approval": False}]
    assert cfg["adapter"] == "unconfigured" and cfg["dataset_path"] == "evals/evals.yaml"


def test_a_fully_configured_spec_renders_no_static_blockers(rendered):
    # adapter and route-evidence blockers come from the adapter's capabilities at run time, so a
    # configured adapter can lift them; release.json only lists configuration that is missing.
    assert release(rendered)["blockers"] == []


def test_unapproved_or_unconfigured_evaluation_still_renders_but_lists_the_blocker(out):
    spec = spec_dict(evaluation={"dataset_status": "proposed"})
    P.render_pipeline(spec, out)
    cfg = _Cfg(json.loads((out / "pipeline" / "release.json").read_text(encoding="utf-8")))
    assert cfg["judge_model"] == "" and cfg["min_pass_rate"] is None and cfg["evaluators"] == []
    assert any("evaluation" in b for b in cfg["blockers"] or [])


def test_router_specs_are_marked_as_routers(out):
    P.render_pipeline(router_dict(), out)
    cfg = _Cfg(json.loads((out / "pipeline" / "release.json").read_text(encoding="utf-8")))
    assert cfg["router"] is True and cfg["pattern"] == "router-specialists"
    assert cfg["case_count"] == 3


def test_a_non_enabled_session_manager_is_listed_as_a_blocker(out):
    spec = dataclasses.replace(spec_dict(), hosted_session_manager="unknown")
    P.render_pipeline(spec, out)
    cfg = _Cfg(json.loads((out / "pipeline" / "release.json").read_text(encoding="utf-8")))
    assert any("session" in b for b in cfg["blockers"] or [])


# --- README -------------------------------------------------------------------------------------


def test_readme_describes_installation_variables_identity_approvals_and_blocked_steps(rendered):
    text = rendered["README.md"]
    for heading in ("Installation", "Variables", "OIDC", "Approvals", "Blocked"):
        assert re.search(rf"(?m)^#+ .*{heading}", text), heading
    for name in ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID", "FOUNDRY_PROJECT_ENDPOINT",
                 "AZURE_AI_MODEL_DEPLOYMENT_NAME"):
        assert name in text
    assert ".github/workflows" in text and "workflow_dispatch" in text and "deploy skill" in text.lower()
    assert re.search(r"(?i)creation (does not|never) (install|dispatch)", text)
    assert "BLOCKED" in text and "federated" in text.lower() and "reviewer" in text.lower()


def test_readme_maps_the_reference_lifecycle_and_names_what_was_not_copied(rendered):
    text = rendered["README.md"]
    assert "validate" in text.lower() and "candidate" in text.lower() and "smoke" in text.lower()
    assert re.search(r"(?i)not (copied|generated)", text) and "APIM" in text


# --- rendered project, end to end ----------------------------------------------------------------


def _project(tmp_path, spec=None):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "agent.py").write_text("def build_agent():\n    return 1\n", encoding="utf-8")
    (root / "main.py").write_text("print('x')\n", encoding="utf-8")
    (root / "evals").mkdir()
    (root / "evals" / "evals.yaml").write_text("cases:\n  - q0\n  - q1\n  - q2\n", encoding="utf-8")
    P.render_pipeline(spec or spec_dict(), root)
    return root


def _clean_env():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def test_rendered_helper_runs_outside_the_source_tree_and_validates(tmp_path):
    root = _project(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-B", str(root / "pipeline" / "hosted_pipeline.py"), "validate", "--root", str(root)],
        cwd=tmp_path, env=_clean_env(), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "adapter" in proc.stdout  # blockers are reported, not hidden


def test_rendered_project_is_blocked_by_its_default_adapter_before_any_azure_login(tmp_path):
    root = _project(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-B", str(root / "pipeline" / "hosted_pipeline.py"), "check", "--root", str(root),
         "--agent", "card-helper", "--environment", "dev"],
        cwd=tmp_path, env={**_clean_env(), **F.good_env()}, capture_output=True, text=True, timeout=60)
    assert proc.returncode == hp.EXIT_CODES[hp.BLOCKED] == 3, proc.stdout + proc.stderr
    assert "BLOCKED" in proc.stdout


def test_rendered_project_releases_end_to_end_through_a_registered_fake_adapter(tmp_path):
    root = _project(tmp_path)
    adapter = F.FakeAdapter()
    code = hp.main(["release", "--root", str(root), "--agent", "card-helper", "--environment", "dev"],
                   adapters={"unconfigured": lambda env: adapter}, env=F.good_env())
    report = json.loads((root / ".pipeline" / "release-report.json").read_text(encoding="utf-8"))
    assert code == 0 and report["status"] == hp.PROMOTED
    assert report["agent"] == "card-helper" and report["run_id"] == "777-2"
    assert report["artifact_id"] == hp.artifact_digest(root)
    assert report["dataset_hash"] == hp.dataset_hash(root / "evals" / "evals.yaml")
    assert adapter.serving == "3"
