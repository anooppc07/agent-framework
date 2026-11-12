"""Task 4 tests (plan cases 31-35, 54-56, 60-61): mode-aware scaffold.

Real files in temp directories, real spec/contract modules, real renderers. The CLI is run as a
subprocess with PYTHONPATH unset from another working directory. Nothing touches Azure or GitHub.
Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

OVERLAY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OVERLAY))

import agent_spec as S  # noqa: E402


def scaffold_module():
    try:
        import scaffold_overlay
        return scaffold_overlay
    except ImportError as exc:
        pytest.fail(f"scaffold_overlay does not import: {exc}")


SPECIALISTS = [
    {"name": "billing", "instructions": 'Answer billing: "charges" only.', "examples": ["Why was I charged twice?"]},
    {"name": "fraud", "instructions": "Handle fraud reports.", "examples": ["I did not make this purchase"]},
]


def spec_dict(**over):
    d = {
        "name": "card-router", "description": 'Routes card questions: "billing" or fraud', "owner": "payments-team",
        "classification": "internal", "agent_kind": "hosted", "framework": "maf", "pattern": "router-specialists",
        "pattern_rationale": "two independent specialists", "model": "gpt-5.4", "hosted_session_manager": "enabled",
        "evals_enabled": False, "pipeline_enabled": False, "specialists": SPECIALISTS,
    }
    d.update(over)
    return d


def write_spec(tmp_path, **over):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec_dict(**over)), encoding="utf-8")
    return path


def tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def cli(tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(OVERLAY / "scaffold_overlay.py"), *args],
                          cwd=tmp_path, env=env, capture_output=True, text=True)


def read(path):
    assert Path(path).exists(), f"{path} was not written"
    return Path(path).read_text(encoding="utf-8")


PLACEHOLDER = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")


# --- case 1-2: create-new router --------------------------------------------------------------

EXPECTED_ROUTER_FILES = {"agent.py", "main.py", "specialists.json", "requirements.txt", "preflight.py",
                         "azure.yaml", ".agentignore", "agent-spec.json",
                         "scaffold-metadata.json", "docs/runtime-contract.md", "docs/runtime-contract.json"}


def test_create_new_router_writes_the_complete_package(tmp_path):
    out = tmp_path / "out"
    written = scaffold_module().scaffold(S.parse_spec(spec_dict()), out, mode="create-new")
    files = set(tree(out))
    assert EXPECTED_ROUTER_FILES <= files, f"missing: {sorted(EXPECTED_ROUTER_FILES - files)}"
    assert {p.resolve() for p in written} == {(out / f).resolve() for f in files}
    for rel in files:
        if rel.endswith((".py", ".json", ".md", ".yaml", ".txt")):
            assert not PLACEHOLDER.search((out / rel).read_text(encoding="utf-8")), f"placeholder left in {rel}"


def test_router_specialists_and_model_are_rendered_from_the_spec(tmp_path):
    out = tmp_path / "out"
    scaffold_module().scaffold(S.parse_spec(spec_dict(model="gpt-test-9")), out, mode="create-new")
    cfg = json.loads(read(out / "specialists.json"))
    assert list(cfg["specialists"]) == ["billing", "fraud"]
    assert cfg["specialists"]["billing"] == 'Answer billing: "charges" only.'
    assert "gpt-test-9" in read(out / "main.py")
    assert "gpt-test-9" in read(out / "azure.yaml")


def test_azure_yaml_is_valid_yaml_with_the_description_intact_and_protocol_2(tmp_path):
    yaml = pytest.importorskip("yaml")
    out = tmp_path / "out"
    scaffold_module().scaffold(S.parse_spec(spec_dict()), out, mode="create-new")
    doc = yaml.safe_load(read(out / "azure.yaml"))
    svc = doc["services"]["card-router"]
    assert svc["description"] == 'Routes card questions: "billing" or fraud'
    assert svc["protocols"] == [{"protocol": "responses", "version": "2.0.0"}]
    assert svc["codeConfiguration"]["runtime"] == "python_3_13"


def test_package_ignore_file_excludes_governance_and_development_files(tmp_path):
    out = tmp_path / "out"
    scaffold_module().scaffold(S.parse_spec(spec_dict()), out, mode="create-new")
    ignore = read(out / ".agentignore").splitlines()
    for entry in ("preflight.py", "tests/", "docs/", "agent-spec.json", "scaffold-metadata.json"):
        assert entry in ignore, f"{entry} should be excluded from the deployed package"


def test_handoff_metadata_records_user_choice_separately_from_the_recommendation(tmp_path):
    out = tmp_path / "out"
    scaffold_module().scaffold(S.parse_spec(spec_dict()), out, mode="create-new")
    meta = json.loads(read(out / "scaffold-metadata.json"))
    assert meta["mode"] == "create-new" and meta["pattern"] == "router-specialists"
    assert meta["entrypoint"] == "main.py" and meta["framework"] == "maf"
    assert meta["session_manager"]["required"] == "enabled" and meta["session_manager"]["observed"] == "not-observed"
    assert meta["deployment"]["performed"] is False and meta["phase"] == "phase-1-local"
    assert meta["selection"] == {"agent_kind": "hosted", "pattern": "router-specialists", "framework": "maf",
                                 "confirmation": "asserted-by-caller"}
    assert meta["rationale"] == "two independent specialists"
    assert "recommendation" in meta and meta["recommendation"] is None, "the CLI cannot know the recommendation"
    stored = json.loads(read(out / "agent-spec.json"))
    assert stored["name"] == "card-router" and "secret" not in json.dumps(stored).lower()


# --- case 3: eval and pipeline selection rules (renderers arrive from Tasks 3b / 7) ------------


def test_pipeline_without_evals_is_rejected_before_any_write(tmp_path):
    import dataclasses
    out = tmp_path / "out"
    forged = dataclasses.replace(S.parse_spec(spec_dict()), pipeline_enabled=True, evals_enabled=False)
    with pytest.raises(S.SpecError, match="pipeline"):
        scaffold_module().scaffold(forged, out, mode="create-new")  # bypasses parse_spec on purpose
    assert not out.exists()


def test_evals_off_means_no_eval_tree_and_no_workflow_directory(tmp_path):
    out = tmp_path / "out"
    scaffold_module().scaffold(S.parse_spec(spec_dict()), out, mode="create-new")
    assert (out / "main.py").exists(), "package must exist before checking what it omits"
    assert not (out / "evals").exists() and not (out / "pipeline").exists()
    assert not any(".github" in f for f in tree(out))


# --- cases 5-6: overlay-existing --------------------------------------------------------------


def seed_legacy(root):
    files = {
        "agent.py": "from langgraph.prebuilt import create_react_agent\n",
        "main.py": "print('legacy host')\n",
        "requirements.txt": "langgraph\n",
        "azure.yaml": "name: legacy\n",
        ".agentignore": ".venv/\n",
        "evals/run_evaluation.py": "# legacy evals\n",
        ".github/workflows/deploy.yml": "name: legacy-deploy\non: push\n",
        "docs/notes.md": "# keep me\n",
        "data/blob.bin": "binary\n",
    }
    for rel, text in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return files


def test_overlay_writes_only_the_contract_and_preserves_every_other_byte(tmp_path):
    target = tmp_path / "legacy"
    seed_legacy(target)
    before = tree(target)
    legacy_spec = S.parse_spec(spec_dict(framework="legacy-langgraph", hosted_session_manager="unknown"))
    written = scaffold_module().scaffold(legacy_spec, target, mode="overlay-existing")
    after = tree(target)
    new = set(after) - set(before)
    assert new == {"docs/runtime-contract.md", "docs/runtime-contract.json"}
    assert {k: v for k, v in after.items() if k in before} == before, "an existing file changed"
    assert {p.resolve() for p in written} == {(target / f).resolve() for f in new}


def test_overlay_does_not_relabel_a_legacy_runtime_as_maf(tmp_path):
    target = tmp_path / "legacy"
    seed_legacy(target)
    scaffold_module().scaffold(S.parse_spec(spec_dict(framework="legacy-langgraph", hosted_session_manager="unknown")),
                               target, mode="overlay-existing")
    data = json.loads(read(target / "docs/runtime-contract.json"))
    assert data["framework"] == "legacy-langgraph" and data["session"]["required"] == "reported-only"
    assert data["claims_implementation"] is False


def test_overlay_requires_an_existing_directory_and_writes_nothing_otherwise(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(S.SpecError, match="exist"):
        scaffold_module().scaffold(S.parse_spec(spec_dict()), missing, mode="overlay-existing")
    assert not missing.exists()
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    with pytest.raises(S.SpecError, match="directory"):
        scaffold_module().scaffold(S.parse_spec(spec_dict()), afile, mode="overlay-existing")


def test_overlay_refuses_to_overwrite_an_existing_contract(tmp_path):
    target = tmp_path / "legacy"
    seed_legacy(target)
    (target / "docs").mkdir(exist_ok=True)
    (target / "docs/runtime-contract.md").write_text("hand written", encoding="utf-8")
    before = tree(target)
    with pytest.raises(S.SpecError, match="exist"):
        scaffold_module().scaffold(S.parse_spec(spec_dict()), target, mode="overlay-existing")
    assert tree(target) == before


# --- cases 7-8: design-only and design-only patterns -------------------------------------------


def test_design_only_writes_a_proposal_and_contract_and_no_executable_assets(tmp_path):
    out = tmp_path / "design"
    scaffold_module().scaffold(S.parse_spec(spec_dict(evals_enabled=True, pipeline_enabled=True)), out,
                               mode="design-only")
    files = set(tree(out))
    assert files == {"docs/runtime-design.md", "docs/runtime-contract.md", "docs/runtime-contract.json"}
    design = read(out / "docs/runtime-design.md")
    assert "unresolved" in design.lower() and "router-specialists" in design


@pytest.mark.parametrize("pattern", ["supervisor-worker", "agent-handoffs"])
def test_design_only_patterns_work_in_design_mode_and_are_refused_elsewhere(tmp_path, pattern):
    s = S.parse_spec(spec_dict(pattern=pattern, specialists=[]))
    scaffold_module().scaffold(s, tmp_path / "d", mode="design-only")
    for mode in ("create-new", "overlay-existing"):
        target = tmp_path / mode
        if mode == "overlay-existing":
            target.mkdir()
        with pytest.raises(S.UnsupportedPattern, match="design-only"):
            scaffold_module().scaffold(s, target, mode=mode)
        assert not any(target.iterdir()) if target.exists() else True


# --- case 9: managed target refusal; no overwrite flag -----------------------------------------


def test_create_new_refuses_a_non_empty_target_before_any_write(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "mine.txt").write_text("keep", encoding="utf-8")
    before = tree(out)
    with pytest.raises(S.SpecError, match="empty"):
        scaffold_module().scaffold(S.parse_spec(spec_dict()), out, mode="create-new")
    assert tree(out) == before


def test_create_new_accepts_an_existing_empty_directory(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    scaffold_module().scaffold(S.parse_spec(spec_dict()), out, mode="create-new")
    assert (out / "main.py").exists()


def test_cli_has_no_overwrite_force_or_migrate_flag(tmp_path):
    r = cli(tmp_path, "--help")
    assert r.returncode == 0 and all(f in r.stdout for f in ("--spec", "--out", "--mode"))
    for flag in ("--force", "--overwrite", "--migrate", "--yes", "--replace"):
        assert flag not in r.stdout


def test_create_new_rejects_what_belongs_to_other_flows(tmp_path):
    for bad, match in ((spec_dict(pattern="single-agent", specialists=[]), "base flow"),
                       (spec_dict(agent_kind="prompt", framework="not-applicable",
                                  hosted_session_manager="not-applicable"), "prompt"),
                       (spec_dict(tools=[{"name": "t", "auth": "none", "requires_approval": False}]), "tool"),
                       (spec_dict(framework="legacy-langgraph"), "maf"),
                       (spec_dict(hosted_session_manager="unknown"), "session")):
        out = tmp_path / f"o{abs(hash(str(bad)))}"
        with pytest.raises(S.SpecError, match=match):
            scaffold_module().scaffold(S.parse_spec(bad), out, mode="create-new")
        assert not out.exists()


# --- case 10-11: containment and atomicity ------------------------------------------------------


def test_unknown_mode_is_rejected_before_any_write(tmp_path):
    with pytest.raises(S.SpecError, match="mode"):
        scaffold_module().scaffold(S.parse_spec(spec_dict()), tmp_path / "o", mode="overwrite")
    assert not (tmp_path / "o").exists()


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs elevated rights on Windows")
def test_symlinked_output_path_is_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(S.SpecError, match="symlink"):
        scaffold_module().scaffold(S.parse_spec(spec_dict()), link, mode="create-new")


def test_rendered_paths_cannot_escape_the_output_directory(tmp_path):
    mod = scaffold_module()
    out = tmp_path / "out"
    out.mkdir()
    for bad in ("../evil.txt", "a/../../evil.txt", "/abs/evil.txt", "C:/evil.txt", "..\\evil.txt"):
        with pytest.raises(S.SpecError, match="outside"):
            mod.contained_path(out, bad)
    assert mod.contained_path(out, "docs/x.md") == (out / "docs" / "x.md").resolve()


def test_a_failure_while_writing_leaves_no_partial_package(tmp_path):
    mod = scaffold_module()
    out = tmp_path / "out"
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file where a directory is needed", encoding="utf-8")
    plan = mod.plan_create_new(S.parse_spec(spec_dict()))
    plan["docs/runtime-contract.md"] = lambda: (_ for _ in ()).throw(RuntimeError("render exploded"))
    with pytest.raises(RuntimeError, match="render exploded"):
        mod.write_plan(out, plan)
    assert not out.exists() or not any(out.rglob("*"))


# --- cases 12-13: CLI, exit codes, outside the source tree --------------------------------------


def test_cli_create_new_succeeds_with_exit_zero(tmp_path):
    spec = write_spec(tmp_path)
    r = cli(tmp_path, "--spec", str(spec), "--out", str(tmp_path / "out"), "--mode", "create-new")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out" / "main.py").exists()
    assert "deploy" not in r.stdout.lower() or "not" in r.stdout.lower()


@pytest.mark.parametrize("args,needle", [
    (["--mode", "overwrite"], "mode"),
    (["--spec", "missing.json", "--mode", "create-new"], "missing.json"),
])
def test_cli_errors_exit_two_with_a_specific_message(tmp_path, args, needle):
    argv = list(args)
    if "--out" not in argv:
        argv += ["--out", str(tmp_path / "o")]
    if "--spec" not in argv:
        argv += ["--spec", str(write_spec(tmp_path))]
    r = cli(tmp_path, *argv)
    assert r.returncode == 2 and needle in (r.stderr + r.stdout)
    assert not (tmp_path / "o").exists()


def test_cli_spec_error_and_conflict_exit_two(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    r = cli(tmp_path, "--spec", str(bad), "--out", str(tmp_path / "o"), "--mode", "create-new")
    assert r.returncode == 2 and "bad.json" in r.stderr
    out = tmp_path / "taken"
    out.mkdir()
    (out / "x").write_text("x", encoding="utf-8")
    r = cli(tmp_path, "--spec", str(write_spec(tmp_path)), "--out", str(out), "--mode", "create-new")
    assert r.returncode == 2 and "empty" in r.stderr


def test_cli_design_only_pattern_in_executable_mode_exits_two(tmp_path):
    spec = write_spec(tmp_path, pattern="supervisor-worker", specialists=[])
    r = cli(tmp_path, "--spec", str(spec), "--out", str(tmp_path / "o"), "--mode", "create-new")
    assert r.returncode == 2 and "design-only" in r.stderr
    r = cli(tmp_path, "--spec", str(spec), "--out", str(tmp_path / "d"), "--mode", "design-only")
    assert r.returncode == 0 and (tmp_path / "d/docs/runtime-design.md").exists()


def test_cli_overlay_mode_preserves_a_legacy_project(tmp_path):
    target = tmp_path / "legacy"
    seed_legacy(target)
    before = tree(target)
    spec = write_spec(tmp_path, framework="legacy-langgraph", hosted_session_manager="unknown")
    r = cli(tmp_path, "--spec", str(spec), "--out", str(target), "--mode", "overlay-existing")
    assert r.returncode == 0, r.stderr
    after = tree(target)
    assert {k: v for k, v in after.items() if k in before} == before
    assert set(after) - set(before) == {"docs/runtime-contract.md", "docs/runtime-contract.json"}


# --- case 14: the generated project actually works ---------------------------------------------


def test_generated_router_imports_and_passes_its_own_preflight_outside_the_source_tree(tmp_path):
    out = tmp_path / "out"
    r = cli(tmp_path, "--spec", str(write_spec(tmp_path)), "--out", str(out), "--mode", "create-new")
    assert r.returncode == 0, r.stderr
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(FOUNDRY_PROJECT_ENDPOINT="https://example.invalid", OTEL_SDK_DISABLED="true",
               AGENTSERVER_STATE_ROOT=str(tmp_path / "state"))
    pf = subprocess.run([sys.executable, "preflight.py", "main.py"], cwd=out, env=env, capture_output=True, text=True)
    assert pf.returncode == 0 and pf.stdout.startswith("enabled"), pf.stdout + pf.stderr
    imp = subprocess.run([sys.executable, "-c", "import main; print(type(main.build_server()).__name__)"],
                         cwd=out, env=env, capture_output=True, text=True)
    assert imp.returncode == 0 and imp.stdout.strip().endswith("ResponsesHostServer"), imp.stderr[-1500:]


def test_generated_router_runs_a_routed_turn_with_the_rendered_specialists(tmp_path):
    out = tmp_path / "out"
    assert cli(tmp_path, "--spec", str(write_spec(tmp_path)), "--out", str(out), "--mode", "create-new").returncode == 0
    code = (
        "import asyncio, json\n"
        "from agent_framework import BaseChatClient, ChatResponse, Message\n"
        "import agent\n"
        "cfg = json.load(open('specialists.json', encoding='utf-8'))\n"
        "class C(BaseChatClient):\n"
        "    def __init__(self, role):\n"
        "        super().__init__(); self.role = role\n"
        "    def _inner_get_response(self, *, messages, stream, options, **kw):\n"
        "        async def go():\n"
        "            text = 'fraud' if self.role == 'classifier' else self.role + '-reply'\n"
        "            return ChatResponse(messages=Message('assistant', [text]))\n"
        "        return go()\n"
        "f = lambda: agent.build_workflow(cfg['specialists'], C, router_instructions=cfg['router_instructions'])\n"
        "r = asyncio.run(agent.run_turn(f, 'I did not make this purchase'))\n"
        "print(r.route, r.outcome, r.answer)\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["OTEL_SDK_DISABLED"] = "true"
    r = subprocess.run([sys.executable, "-c", code], cwd=out, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "fraud answered fraud-reply", r.stderr[-1500:]


# --- reported by the Task 3b agent: the routing evaluation runner is a development file ----------


def test_the_ignore_file_excludes_every_development_only_file_that_could_be_rendered(tmp_path):
    out = tmp_path / "out"
    scaffold_module().scaffold(S.parse_spec(spec_dict()), out, mode="create-new")
    ignore = read(out / ".agentignore").splitlines()
    for entry in ("run_routing_evaluation.py", "evals/", "pipeline/", "docs/", "tests/", "preflight.py",
                  "agent-spec.json", "scaffold-metadata.json"):
        assert entry in ignore, f"{entry} must not ship in the deployed package"


def test_nothing_the_package_needs_to_run_is_excluded(tmp_path):
    out = tmp_path / "out"
    scaffold_module().scaffold(S.parse_spec(spec_dict()), out, mode="create-new")
    ignore = set(read(out / ".agentignore").splitlines())
    for needed in ("agent.py", "main.py", "specialists.json", "requirements.txt", "azure.yaml"):
        assert needed not in ignore and not any(needed.startswith(e) for e in ignore if e.endswith("/"))
