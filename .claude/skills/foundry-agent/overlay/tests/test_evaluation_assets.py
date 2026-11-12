"""Task 3b shared evaluation assets (plan cases 50-52, 56; brief cases 1, 13): canonical evals.yaml, tests.yaml,
derived JSONL datasets and source-manifest composition for a hosted single-agent.

Real spec parsing, rendering and filesystem; no mocks. Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

OVERLAY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OVERLAY))

import agent_spec as A  # noqa: E402
import evaluation_assets as E  # noqa: E402

SKILL = OVERLAY.parent
EVALS_SRC = SKILL / "hosted" / "assets" / "evals"
NASTY = 'Why is "Card #4242: pending"? it\'s odd — 日本語 \U0001f642 nel\x85 del\x7f  '


def case(i, **over):
    c = {"query": f"How do I dispute charge number {i}?"}
    c.update(over)
    return c


def spec_dict(**over):
    d = {
        "name": "card-helper", "description": "Dispute intake", "owner": "payments-team",
        "classification": "internal", "agent_kind": "hosted", "framework": "maf", "pattern": "single-agent",
        "pattern_rationale": "one agent is enough", "model": "gpt-5.4", "hosted_session_manager": "enabled",
        "evals_enabled": True,
        "evaluation": {"judge_model": "judge-deploy", "min_pass_rate": 0.8, "evaluators": ["relevance", "coherence"],
                       "dataset_status": "approved", "cases": [case(i) for i in range(1, 5)]},
    }
    d.update(over)
    return d


def spec(**over):
    return A.parse_spec(spec_dict(**over))


def with_eval(**over):
    ev = dict(spec_dict()["evaluation"])
    ev.update(over)
    return spec(evaluation=ev)


def snapshot(root):
    """Relative path -> sha256 of every file under root (caches excluded)."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and not ({"__pycache__", ".pytest_cache"} & set(p.parts)) and p.suffix != ".pyc":
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def render(tmp_path, s=None, name="out"):
    out = tmp_path / name
    return E.render_eval_tree(s or spec(), out), out


def rd(path):
    """Read a generated file; a file the renderer failed to write is an assertion failure, not an OSError."""
    assert Path(path).is_file(), f"the renderer did not write {path}"
    return Path(path).read_text(encoding="utf-8")


def jsonl(path):
    return [json.loads(line) for line in rd(path).split("\n") if line.strip()]


# --- brief case 1: evals.yaml is safe YAML that round-trips ---------------------------------------------


def test_evals_yaml_round_trips_nasty_text_through_safe_load(tmp_path):
    s = with_eval(cases=[case(1, query=NASTY), case(2), case(3)], judge_model="judge: \"deploy\" #1")
    _, out = render(tmp_path, s)
    data = yaml.safe_load(rd(out / "evals" / "evals.yaml"))
    assert data["dataset"][0]["query"] == NASTY
    assert data["judge_model"] == "judge: \"deploy\" #1"
    assert data["agent"] == "card-helper" and data["pattern"] == "single-agent"


def test_yaml_scalar_values_that_look_like_other_types_stay_strings(tmp_path):
    odd = ["null", "~", "true", "0x1F", "1e3", "- dash", "[x]", "{a: b}", "key: value", "# comment", "2024-01-01"]
    s = with_eval(cases=[case(i, query=q) for i, q in enumerate(odd)])
    _, out = render(tmp_path, s)
    data = yaml.safe_load(rd(out / "evals" / "evals.yaml"))
    assert [c["query"] for c in data["dataset"]] == odd and all(isinstance(c["query"], str) for c in data["dataset"])


def test_render_fields_follow_the_reference_names(tmp_path):
    _, out = render(tmp_path)
    data = yaml.safe_load(rd(out / "evals" / "evals.yaml"))
    assert data["judge_model"] == "judge-deploy" and data["min_pass_rate"] == 0.8
    assert data["evaluators"] == ["relevance", "coherence"] and data["dataset_status"] == "approved"
    assert [c["case_id"] for c in data["dataset"]] == ["c001", "c002", "c003", "c004"]
    assert data["smoke"] == ["c001", "c002", "c003", "c004"]


# --- E2/E4: unconfigured stays explicit and is never invented -----------------------------------------


def test_an_unconfigured_spec_renders_explicit_nulls_and_empty_lists_not_defaults(tmp_path):
    s = spec(evaluation=None)
    _, out = render(tmp_path, s)
    data = yaml.safe_load(rd(out / "evals" / "evals.yaml"))
    assert data["judge_model"] is None and data["min_pass_rate"] is None
    assert data["evaluators"] == [] and data["dataset"] == [] and data["smoke"] == []
    assert data["dataset_status"] == "proposed"
    assert rd(out / "evals" / "datasets" / "baseline.jsonl") == ""
    assert "UNCONFIGURED" in rd(out / "evals" / "evals.yaml")


def test_a_rendered_unconfigured_tree_is_blocked_by_the_release_gate(tmp_path):
    _, out = render(tmp_path, spec(evaluation=None))
    assert (out / "evals" / "evals.yaml").is_file(), "the renderer did not write evals.yaml"
    sys.path.insert(0, str(EVALS_SRC))
    try:
        import run_release_evaluation as gate
        with pytest.raises(gate.ConfigError, match="unconfigured"):
            gate.load_config(out / "evals" / "evals.yaml")
    finally:
        sys.path.remove(str(EVALS_SRC))
        sys.modules.pop("run_release_evaluation", None)


def test_a_rendered_configured_tree_is_accepted_by_the_release_gate(tmp_path):
    _, out = render(tmp_path)
    assert (out / "evals" / "evals.yaml").is_file(), "the renderer did not write evals.yaml"
    sys.path.insert(0, str(EVALS_SRC))
    try:
        import run_release_evaluation as gate
        config = gate.load_config(out / "evals" / "evals.yaml")
        assert config.agent == "card-helper" and len(config.rows) == 4
        request = gate.build_request(config, "card-helper", "1")
        assert request.items[0]["case_id"] == "c001"
    finally:
        sys.path.remove(str(EVALS_SRC))
        sys.modules.pop("run_release_evaluation", None)


def test_evals_disabled_produces_no_eval_tree_and_creates_nothing(tmp_path):
    result, out = render(tmp_path, spec(evals_enabled=False, evaluation=None))
    assert result.files == () and result.plan is None
    assert not out.exists(), "an evals-disabled spec must not even create the output directory"


# --- E3: stability -------------------------------------------------------------------------------------


def test_rendering_twice_gives_byte_identical_trees_and_stable_case_ids(tmp_path):
    render(tmp_path, name="a")
    render(tmp_path, name="b")
    assert snapshot(tmp_path / "a") == snapshot(tmp_path / "b") and len(snapshot(tmp_path / "a")) >= 8


def test_jsonl_keeps_non_ascii_text_readable_not_escaped(tmp_path):
    s = with_eval(cases=[case(1, query="¿Por qué? 日本語"), case(2), case(3)])
    _, out = render(tmp_path, s)
    raw = rd(out / "evals" / "datasets" / "baseline.jsonl")
    assert "日本語" in raw and '\\u65e5' not in raw  # kept readable, not \uXXXX-escaped


def test_case_ids_follow_source_order_and_do_not_depend_on_query_text(tmp_path):
    s = with_eval(cases=[case(1, query="zebra"), case(2, query="apple"), case(3, query="mango")])
    _, out = render(tmp_path, s)
    rows = jsonl(out / "evals" / "datasets" / "baseline.jsonl")
    assert [(r["case_id"], r["query"]) for r in rows] == [("c001", "zebra"), ("c002", "apple"), ("c003", "mango")]


# --- datasets: baseline is every case once; smoke is a capped subset -----------------------------------


def test_baseline_has_every_approved_case_exactly_once_with_its_context(tmp_path):
    cases = [case(i, context="approved policy excerpt") for i in range(1, 5)]
    _, out = render(tmp_path, with_eval(cases=cases, evaluators=["relevance", "groundedness"]))
    rows = jsonl(out / "evals" / "datasets" / "baseline.jsonl")
    assert [r["query"] for r in rows] == [c["query"] for c in cases]
    assert all(r["context"] == "approved policy excerpt" for r in rows)
    assert len({r["case_id"] for r in rows}) == len(rows)


def test_single_agent_smoke_is_capped_at_five_and_is_a_byte_exact_subset_of_baseline(tmp_path):
    s = with_eval(cases=[case(i) for i in range(1, 9)])
    _, out = render(tmp_path, s)
    base_lines = rd(out / "evals" / "datasets" / "baseline.jsonl").split("\n")
    smoke_lines = rd(out / "evals" / "datasets" / "ci-smoke.jsonl").split("\n")
    assert len(smoke_lines) - 1 == 5 and len(base_lines) - 1 == 8
    assert all(line in base_lines for line in smoke_lines if line)
    assert yaml.safe_load(rd(out / "evals" / "evals.yaml"))["smoke"] == [
        f"c00{i}" for i in range(1, 6)]


# --- tests.yaml: smoke intent, not a security gate ------------------------------------------------------


def test_tests_yaml_records_smoke_intent_using_the_reference_fields(tmp_path):
    _, out = render(tmp_path)
    text = rd(out / "evals" / "tests.yaml")
    data = yaml.safe_load(text)
    assert isinstance(data, list) and len(data) == 4
    assert data[0]["input"] == "How do I dispute charge number 1?" and data[0]["case_id"] == "c001"
    assert data[0]["must_contain_any"] == [] and "UNCONFIGURED" in text
    assert "not a security" in text.lower() and "substring" in text.lower()


def test_tests_yaml_round_trips_nasty_text(tmp_path):
    _, out = render(tmp_path, with_eval(cases=[case(1, query=NASTY), case(2), case(3)]))
    data = yaml.safe_load(rd(out / "evals" / "tests.yaml"))
    assert data[0]["input"] == NASTY


# --- case 51: preflight failures leave no output --------------------------------------------------------


def test_an_unsupported_evaluator_fails_before_any_output(tmp_path):
    with pytest.raises(E.EvalAssetError, match="tool_call_accuracy"):
        render(tmp_path, with_eval(evaluators=["relevance", "tool_call_accuracy"]))
    assert not (tmp_path / "out").exists()


def test_groundedness_without_context_on_every_case_fails_before_output(tmp_path):
    cases = [case(1, context="policy"), case(2), case(3)]
    with pytest.raises(E.EvalAssetError, match="groundedness"):
        render(tmp_path, with_eval(cases=cases, evaluators=["groundedness"]))
    assert not (tmp_path / "out").exists()


def test_a_missing_source_asset_fails_before_any_output_and_the_source_is_untouched(tmp_path):
    fake_root = tmp_path / "skill"
    shutil.copytree(SKILL / "hosted", fake_root / "hosted", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (fake_root / "hosted" / "assets" / "evals" / "run_release_evaluation.py").unlink()
    before = snapshot(fake_root)
    with pytest.raises(E.EvalAssetError, match="run_release_evaluation.py"):
        E.render_eval_tree(spec(), tmp_path / "out", skill_root=fake_root)
    assert not (tmp_path / "out").exists() and snapshot(fake_root) == before


def test_an_existing_destination_file_is_a_conflict_and_nothing_is_written(tmp_path):
    out = tmp_path / "out"
    (out / "evals").mkdir(parents=True)
    (out / "evals" / "evals.yaml").write_text("mine: true\n", encoding="utf-8")
    before = snapshot(out)
    with pytest.raises(E.EvalAssetError, match="evals.yaml"):
        E.render_eval_tree(spec(), out)
    assert snapshot(out) == before


def test_an_output_path_that_is_a_file_fails_without_a_partial_tree(tmp_path):
    blocker = tmp_path / "out"
    blocker.write_text("a file", encoding="utf-8")
    with pytest.raises(E.EvalAssetError):
        E.render_eval_tree(spec(), blocker)
    assert blocker.read_text(encoding="utf-8") == "a file"


def test_a_file_where_a_directory_is_needed_is_a_conflict_and_nothing_is_written(tmp_path):
    out = tmp_path / "out"
    (out / "evals").mkdir(parents=True)
    (out / "evals" / "datasets").write_text("i am a file", encoding="utf-8")
    before = snapshot(out)
    with pytest.raises(E.EvalAssetError, match="datasets"):
        E.render_eval_tree(spec(), out)
    assert snapshot(out) == before, "a late failure must not leave a partial tree behind"


def test_text_that_cannot_be_encoded_as_utf8_fails_before_output(tmp_path):
    with pytest.raises(E.EvalAssetError, match="UTF-8"):
        render(tmp_path, with_eval(cases=[case(1, query="lone surrogate \ud800"), case(2), case(3)]))
    assert not (tmp_path / "out").exists()


def test_a_prompt_agent_is_rejected_on_its_kind_even_with_a_maf_framework_forged_in(tmp_path):
    import dataclasses
    forged = dataclasses.replace(spec(), agent_kind="prompt")
    assert forged.framework == "maf", "the kind check must be what rejects this, not the framework check"
    with pytest.raises(E.EvalAssetError, match="hosted"):
        render(tmp_path, forged)
    assert not (tmp_path / "out").exists()


def test_a_non_maf_hosted_agent_is_rejected_on_its_framework(tmp_path):
    with pytest.raises(E.EvalAssetError, match="maf"):
        render(tmp_path, spec(framework="legacy-langgraph"))
    assert not (tmp_path / "out").exists()


def test_design_only_patterns_are_rejected(tmp_path):
    with pytest.raises(E.EvalAssetError):
        render(tmp_path, spec(pattern="supervisor-worker"))


# --- source manifest: explicit, no caches, no legacy runner ---------------------------------------------


def test_the_tree_is_composed_from_the_explicit_manifest_and_nothing_else(tmp_path):
    result, out = render(tmp_path)
    files = sorted(snapshot(out))
    assert files == sorted([
        "evals/.gitignore", "evals/README.md", "evals/datasets/baseline.jsonl", "evals/datasets/ci-smoke.jsonl",
        "evals/evals.yaml", "evals/requirements.txt", "evals/run_release_evaluation.py",
        "evals/tests.yaml", "evals/tests/test_release_evaluation.py"])
    assert sorted(f.relative_to(out).as_posix() for f in result.files) == files


def test_the_legacy_runner_evaluators_and_datasets_are_not_copied(tmp_path):
    _, out = render(tmp_path)
    assert (out / "evals" / "run_release_evaluation.py").is_file(), "nothing was rendered, so absence proves nothing"
    assert not (out / "evals" / "run_evaluation.py").exists() and not (out / "evals" / "evaluators").exists()
    assert not (out / "evals" / "tests" / "test_hosted_template.py").exists()


def test_copied_runner_files_are_byte_identical_to_their_sources(tmp_path):
    _, out = render(tmp_path)
    for rel in ("run_release_evaluation.py", "tests/test_release_evaluation.py", ".gitignore"):
        assert (out / "evals" / rel).is_file(), f"the renderer did not write {rel}"
        assert (out / "evals" / rel).read_bytes() == (EVALS_SRC / rel).read_bytes(), rel


def test_rendering_does_not_mutate_the_skill_sources_or_copy_caches(tmp_path):
    before = snapshot(SKILL / "hosted")
    cache = EVALS_SRC / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "junk.cpython-313.pyc").write_bytes(b"x")
    try:
        _, out = render(tmp_path)
    finally:
        shutil.rmtree(cache, ignore_errors=True)
    assert snapshot(SKILL / "hosted") == before
    assert (out / "evals" / "run_release_evaluation.py").is_file(), "nothing was rendered, so absence proves nothing"
    leaked = [p for p in out.rglob("*") if "__pycache__" in p.parts or p.suffix == ".pyc" or ".pytest_cache" in p.parts]
    assert leaked == []


def test_the_generated_requirements_are_explicit_and_have_no_langgraph(tmp_path):
    _, out = render(tmp_path)
    reqs = rd(out / "evals" / "requirements.txt").lower()
    for needed in ("azure-ai-projects", "azure-identity", "pyyaml", "pytest"):
        assert needed in reqs
    assert "langgraph" not in reqs and "langchain" not in reqs


def test_the_readme_labels_unconfigured_values_and_the_evidence_scope(tmp_path):
    _, out = render(tmp_path, spec(evaluation=None))
    text = rd(out / "evals" / "README.md")
    assert "UNCONFIGURED" in text and "does not promote" in text.lower()
    assert "github.com/DeepuMM/agent-deployment" in text and "2220a3c2e3964728aef4444048944db0ebf41ecc" in text


# --- brief case 13: generated evals run outside the skill tree ------------------------------------------


def test_generated_tests_run_outside_the_skill_tree_with_pythonpath_unset(tmp_path):
    _, out = render(tmp_path)
    assert (out / "evals" / "tests" / "test_release_evaluation.py").is_file(), "the renderer did not write the tests"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    run = subprocess.run([sys.executable, "-m", "pytest", "evals/tests", "-q", "--no-header", "-p", "no:cacheprovider",
                          "-p", "no:randomly"], cwd=out, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout[-1500:] + run.stderr[-800:]
    assert "passed" in run.stdout and str(SKILL) not in run.stdout


def test_the_generated_runner_blocks_from_a_copy_when_the_judge_is_unconfigured(tmp_path):
    _, out = render(tmp_path, spec(evaluation=None))
    assert (out / "evals" / "run_release_evaluation.py").is_file(), "the renderer did not write the runner"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    run = subprocess.run([sys.executable, "evals/run_release_evaluation.py", "--config", "evals/evals.yaml",
                          "--agent-name", "card-helper", "--agent-version", "1"],
                         cwd=out, env=env, capture_output=True, text=True)
    assert run.returncode == 2 and "unconfigured" in run.stderr.lower(), (run.stdout, run.stderr)


def test_the_renderer_and_the_hosted_gate_support_exactly_the_same_evaluators():
    sys.path.insert(0, str(EVALS_SRC))
    try:
        import run_release_evaluation as gate
        assert set(E.SUPPORTED_EVALUATORS) == set(gate.SUPPORTED_EVALUATORS)
        assert set(E.NEEDS_CONTEXT) == {n for n, v in gate.SUPPORTED_EVALUATORS.items() if v["needs_context"]}
    finally:
        sys.path.remove(str(EVALS_SRC))
        sys.modules.pop("run_release_evaluation", None)
