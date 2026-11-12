"""Evals and the release pipeline are selectable at creation and generated into the same all-or-nothing
write as the rest of the package. Real renderers, real files; nothing is mocked or deployed.
Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

OVERLAY = Path(__file__).resolve().parent.parent
SKILL = OVERLAY.parent
PRESET = SKILL / "examples" / "card-dispute" / "preset.json"
sys.path.insert(0, str(OVERLAY))

import agent_spec as S  # noqa: E402
import intake  # noqa: E402
import scaffold_overlay as SC  # noqa: E402


def spec_dict(**over):
    d = {
        "name": "card-router", "description": 'Routes: "billing" or fraud', "owner": "payments-team",
        "classification": "internal", "agent_kind": "hosted", "framework": "maf", "pattern": "router-specialists",
        "pattern_rationale": "two independent specialists", "model": "gpt-5-mini", "hosted_session_manager": "enabled",
        "evals_enabled": True, "pipeline_enabled": False,
        "specialists": [{"name": "billing", "instructions": "Answer billing questions.", "examples": ["Why was I charged twice?"]},
                        {"name": "fraud", "instructions": "Handle fraud reports.", "examples": ["I did not make this purchase"]}],
        "out_of_scope_examples": ["What is the weather in Paris?"],
    }
    d.update(over)
    return d


def create(tmp_path, **over):
    out = tmp_path / "out"
    written = SC.scaffold(S.parse_spec(spec_dict(**over)), out, mode="create-new")
    return out, written


def files(out):
    return {p.relative_to(out).as_posix() for p in Path(out).rglob("*") if p.is_file()}


# --- availability is real, not a flag that is merely flipped ------------------------------------


def test_extras_are_available_now():
    assert SC.EXTRAS_AVAILABLE is True


# --- evals ---------------------------------------------------------------------------------------


def test_evals_selected_writes_the_evaluation_tree(tmp_path):
    out, _ = create(tmp_path)
    got = files(out)
    assert {"evals/evals.yaml", "evals/tests.yaml", "evals/datasets/baseline.jsonl", "evals/datasets/ci-smoke.jsonl",
            "evals/run_release_evaluation.py", "evals/README.md", "run_routing_evaluation.py"} <= got
    assert "main.py" in got and "docs/runtime-contract.md" in got


def test_evals_off_writes_no_eval_files(tmp_path):
    out, _ = create(tmp_path, evals_enabled=False)
    assert not any(f.startswith("evals/") or "routing_evaluation" in f for f in files(out))


def test_canonical_evals_yaml_carries_the_reference_fields_and_unconfigured_markers(tmp_path):
    out, _ = create(tmp_path)
    text = (out / "evals" / "evals.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert {"judge_model", "min_pass_rate", "evaluators", "dataset"} <= set(doc)
    # no judge, threshold or evaluators were given, so none may be invented: explicit nulls, each flagged in the file
    assert doc["judge_model"] is None and doc["min_pass_rate"] is None and doc["evaluators"] == []
    for key in ("judge_model", "min_pass_rate", "evaluators"):
        line = next(ln for ln in text.splitlines() if ln.startswith(key + ":"))
        assert "UNCONFIGURED" in line, f"{key} must be flagged as unconfigured in the file"
    queries = [c["query"] for c in doc["dataset"]]
    assert "Why was I charged twice?" in queries and "What is the weather in Paris?" in queries
    assert len(queries) == len(set(queries))


def test_derived_datasets_agree_with_the_canonical_file(tmp_path):
    out, _ = create(tmp_path)
    doc = yaml.safe_load((out / "evals" / "evals.yaml").read_text(encoding="utf-8"))
    base = [json.loads(line) for line in (out / "evals/datasets/baseline.jsonl").read_text(encoding="utf-8").split("\n") if line]
    assert [r["query"] for r in base] == [c["query"] for c in doc["dataset"]]
    routes = {r["query"]: r["expected_route"] for r in base}
    assert routes["What is the weather in Paris?"] == "refusal" and routes["Why was I charged twice?"] == "billing"
    smoke = [json.loads(line) for line in (out / "evals/datasets/ci-smoke.jsonl").read_text(encoding="utf-8").split("\n") if line]
    assert 2 <= len(smoke) <= 5 and {r["query"] for r in smoke} <= {r["query"] for r in base}


def test_evals_for_a_router_need_examples_and_fail_before_any_write(tmp_path):
    out = tmp_path / "out"
    bad = spec_dict(specialists=[{"name": "billing", "instructions": "x", "examples": []},
                                 {"name": "fraud", "instructions": "y", "examples": []}], out_of_scope_examples=[])
    with pytest.raises(S.SpecError, match="example"):
        SC.scaffold(S.parse_spec(bad), out, mode="create-new")
    assert not out.exists()


# --- pipeline ------------------------------------------------------------------------------------


def test_pipeline_selected_stages_files_inside_the_agent_folder(tmp_path):
    out, _ = create(tmp_path, pipeline_enabled=True)
    got = files(out)
    assert {"pipeline/agent-ci.yml", "pipeline/agent-deploy.yml", "pipeline/hosted_pipeline.py",
            "pipeline/release.json", "pipeline/README.md"} <= got
    assert not any(".github" in f.split("/") or "workflows" in f.split("/") for f in got)


def test_pipeline_off_writes_no_pipeline_files(tmp_path):
    out, _ = create(tmp_path, pipeline_enabled=False)
    assert not any(f.startswith("pipeline/") for f in files(out))


def test_staged_workflows_are_manual_only_and_not_installed(tmp_path):
    out, _ = create(tmp_path, pipeline_enabled=True)
    deploy = yaml.safe_load((out / "pipeline" / "agent-deploy.yml").read_text(encoding="utf-8"))
    on = deploy.get("on", deploy.get(True))
    assert set(on) == {"workflow_dispatch"}
    ci = yaml.safe_load((out / "pipeline" / "agent-ci.yml").read_text(encoding="utf-8"))
    assert set(ci.get("on", ci.get(True))) == {"pull_request"}


def test_pipeline_points_at_the_eval_file_this_scaffold_wrote(tmp_path):
    out, _ = create(tmp_path, pipeline_enabled=True)
    release = json.loads((out / "pipeline" / "release.json").read_text(encoding="utf-8"))
    assert release["dataset_path"] == "evals/evals.yaml" and (out / "evals" / "evals.yaml").exists()


def test_the_generated_pipeline_blocks_instead_of_pretending_a_release_is_ready(tmp_path):
    out, _ = create(tmp_path, pipeline_enabled=True)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, "pipeline/hosted_pipeline.py", "validate", "--root", "."], cwd=out, env=env,
                       capture_output=True, text=True)
    assert r.returncode in (0, 2, 3), r.stderr
    check = subprocess.run([sys.executable, "pipeline/hosted_pipeline.py", "check", "--root", ".", "--agent", "card-router",
                            "--environment", "dev"], cwd=out, env=env, capture_output=True, text=True)
    assert check.returncode != 0, "an unconfigured release must not report ready"


def test_pipeline_without_evals_is_still_rejected_before_any_write(tmp_path):
    import dataclasses
    out = tmp_path / "out"
    forged = dataclasses.replace(S.parse_spec(spec_dict()), pipeline_enabled=True, evals_enabled=False)
    with pytest.raises(S.SpecError, match="pipeline"):
        SC.scaffold(forged, out, mode="create-new")
    assert not out.exists()


# --- one write, one contract, one metadata -----------------------------------------------------


def test_everything_is_written_together_and_listed(tmp_path):
    out, written = create(tmp_path, pipeline_enabled=True)
    rels = {Path(p).resolve().relative_to(out.resolve()).as_posix() for p in written}
    assert rels == files(out)


def test_contract_and_metadata_record_the_selection(tmp_path):
    out, _ = create(tmp_path, pipeline_enabled=True)
    meta = json.loads((out / "scaffold-metadata.json").read_text(encoding="utf-8"))
    assert meta["evaluation"] == "unconfigured" and meta["pipeline"] == "selected-not-executed"
    assert meta["deployment"]["performed"] is False
    contract = (out / "docs" / "runtime-contract.md").read_text(encoding="utf-8")
    assert "Evaluation: unconfigured" in contract and "selected-not-executed" in contract
    assert "Claims implementation: no" in contract


def test_a_failure_in_any_part_leaves_no_partial_package(tmp_path, monkeypatch):
    import pipeline_assets
    out = tmp_path / "out"

    def boom(*a, **k):
        raise S.SpecError("pipeline source missing")
    monkeypatch.setattr(pipeline_assets, "pipeline_files", boom)
    with pytest.raises(S.SpecError, match="pipeline source missing"):
        SC.scaffold(S.parse_spec(spec_dict(pipeline_enabled=True)), out, mode="create-new")
    assert not out.exists()


def test_the_package_excludes_the_development_files_from_deployment(tmp_path):
    out, _ = create(tmp_path, pipeline_enabled=True)
    ignore = (out / ".agentignore").read_text(encoding="utf-8").splitlines()
    for entry in ("evals/", "pipeline/", "run_routing_evaluation.py", "docs/"):
        assert entry in ignore


# --- the intake offers and records the choice ---------------------------------------------------


def preset():
    return intake.load_preset(PRESET)


def test_the_intake_now_asks_about_evals_and_pipeline():
    ids = [q["id"] for b in intake.questions(preset()) for q in b]
    assert "evals" in ids and "pipeline" in ids


def test_intake_builds_a_spec_with_both_selected():
    spec = intake.build_spec(preset(), {"owner": "o", "evals": "yes", "pipeline": "yes"})
    assert spec["evals_enabled"] is True and spec["pipeline_enabled"] is True
    S.validate_for_mode(S.parse_spec(spec), "create-new")


def test_intake_end_to_end_with_both_selected(tmp_path):
    spec = intake.build_spec(preset(), {"owner": "o", "evals": "yes", "pipeline": "yes"})
    out = tmp_path / "agent"
    result = intake.dispatch(spec, {"confirmed": True, "agent_kind": "hosted", "pattern": "router-specialists"}, out)
    assert result.action == "created"
    got = files(out)
    assert "evals/evals.yaml" in got and "pipeline/agent-deploy.yml" in got and "docs/adr/0005-evaluation.md" in got


def test_the_evaluation_record_says_evals_were_generated(tmp_path):
    out, _ = create(tmp_path)
    text = (out / "docs" / "adr" / "0005-evaluation.md").read_text(encoding="utf-8")
    assert "Evals on" in text or "generated" in text.lower()


def test_the_preset_defaults_evals_and_pipeline_on_so_the_demo_shows_the_full_package():
    d = preset()["defaults"]
    assert d["evals_enabled"] is True and d["pipeline_enabled"] is True
    by_id = {q["id"]: q for b in intake.questions(preset()) for q in b}
    for qid in ("evals", "pipeline"):
        assert by_id[qid]["options"][0]["value"] == "yes" and by_id[qid]["options"][0]["label"].endswith("(Recommended)")


def test_accepting_every_preset_default_still_builds_and_creates_a_valid_agent(tmp_path):
    spec = intake.build_spec(preset(), {"owner": "payments-team"})
    assert spec["evals_enabled"] is True and spec["pipeline_enabled"] is True
    result = intake.dispatch(spec, {"confirmed": True, "agent_kind": "hosted", "pattern": "router-specialists"}, tmp_path / "a")
    assert result.action == "created"
    assert (tmp_path / "a" / "evals" / "evals.yaml").exists() and (tmp_path / "a" / "pipeline" / "agent-deploy.yml").exists()


def test_a_user_can_still_override_evals_and_pipeline_off():
    spec = intake.build_spec(preset(), {"owner": "o", "evals": "no", "pipeline": "no"})
    assert spec["evals_enabled"] is False and spec["pipeline_enabled"] is False


def test_pipeline_still_requires_evals_when_the_user_turns_evals_off():
    with pytest.raises(S.SpecError, match="pipeline"):
        intake.build_spec(preset(), {"owner": "o", "evals": "no", "pipeline": "yes"})
    with pytest.raises(S.SpecError, match="pipeline"):
        intake.build_spec(preset(), {"owner": "o", "evals": "no"})  # the inherited pipeline default needs evals too
    assert intake.build_spec(preset(), {"owner": "o", "evals": "no", "pipeline": "no"})["pipeline_enabled"] is False
