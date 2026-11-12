"""Task 3b router evaluation assets (plan cases 45-49, 57-59, 64; brief cases 2-6): baseline and smoke datasets
derived from one plan, reserved refusal route, coverage reporting, conflict rejection, and a generated router
eval tree that runs outside the skill tree.

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
import router_assets as R  # noqa: E402

SKILL = OVERLAY.parent
ROUTER_SRC = SKILL / "hosted" / "patterns" / "router-specialists"


def specialist(name, *examples):
    return {"name": name, "instructions": f"You handle {name} requests.", "examples": list(examples)}


def spec_dict(specialists=None, oos=None, cases=None, **over):
    d = {
        "name": "card-router", "description": "Routes card requests", "owner": "payments-team",
        "classification": "internal", "agent_kind": "hosted", "framework": "maf", "pattern": "router-specialists",
        "pattern_rationale": "two independent topics", "model": "gpt-5.4", "hosted_session_manager": "enabled",
        "evals_enabled": True,
        "specialists": specialists if specialists is not None else [
            specialist("billing", "Why was I charged twice?"), specialist("fraud", "I did not make this purchase")],
        "evaluation": {"judge_model": "judge-deploy", "min_pass_rate": 0.8, "evaluators": ["relevance"],
                       "dataset_status": "approved", "cases": cases or []},
    }
    if oos is not None:
        d["out_of_scope_examples"] = oos
    d.update(over)
    return d


def spec(**kw):
    return A.parse_spec(spec_dict(**kw))


def rd(path):
    assert Path(path).is_file(), f"the renderer did not write {path}"
    return Path(path).read_text(encoding="utf-8")


def rows(path):
    return [json.loads(line) for line in rd(path).split("\n") if line.strip()]


def render(tmp_path, s, name="out"):
    out = tmp_path / name
    return E.render_eval_tree(s, out), out


def datasets(out):
    base = rows(out / "evals" / "datasets" / "baseline.jsonl")
    smoke = rows(out / "evals" / "datasets" / "ci-smoke.jsonl")
    return base, smoke


def snapshot(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and not ({"__pycache__", ".pytest_cache"} & set(p.parts)) and p.suffix != ".pyc":
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# --- brief case 2 / plan 47: baseline has every approved case exactly once ---------------------------------


def test_baseline_has_every_approved_input_exactly_once_with_case_id_and_expected_route(tmp_path):
    s = spec(oos=["What is the weather on Mars?"],
             cases=[{"query": "Refund my duplicate fee", "expected_route": "billing"}])
    _, out = render(tmp_path, s)
    base, _ = datasets(out)
    assert [(r["query"], r["expected_route"]) for r in base] == [
        ("Why was I charged twice?", "billing"), ("I did not make this purchase", "fraud"),
        ("What is the weather on Mars?", "refusal"), ("Refund my duplicate fee", "billing")]
    assert [r["case_id"] for r in base] == ["c001", "c002", "c003", "c004"]
    assert len({r["query"] for r in base}) == len(base)


def test_queries_are_preserved_verbatim_including_whitespace_and_unicode(tmp_path):
    odd = "  ¿Por qué me cobraron dos veces?  日本語\U0001f642 \"quoted\"  "
    _, out = render(tmp_path, spec(specialists=[specialist("billing", odd), specialist("fraud", "fraud q")]))
    base, _ = datasets(out)
    assert base[0]["query"] == odd


def test_the_plan_is_the_single_source_for_yaml_baseline_and_smoke(tmp_path):
    s = spec(oos=["Tell me a joke"])
    plan = R.build_router_plan(s)
    _, out = render(tmp_path, s)
    data = yaml.safe_load(rd(out / "evals" / "evals.yaml"))
    base, smoke = datasets(out)
    assert [c["case_id"] for c in data["dataset"]] == [c.case_id for c in plan.cases] == [r["case_id"] for r in base]
    assert [c["expected_route"] for c in data["dataset"]] == [r["expected_route"] for r in base]
    assert data["smoke"] == list(plan.smoke_ids) == [r["case_id"] for r in smoke]
    assert data["routes"] == ["billing", "fraud", "refusal"] and data["pattern"] == "router-specialists"


# --- brief case 3 / plan 45: one specialist plus one out-of-scope gives two rows ---------------------------


def test_one_specialist_and_one_out_of_scope_example_give_a_two_row_smoke_set(tmp_path):
    s = spec(specialists=[specialist("billing", "Why was I charged twice?")], oos=["What is the weather on Mars?"])
    _, out = render(tmp_path, s)
    _, smoke = datasets(out)
    assert [(r["expected_route"]) for r in smoke] == ["billing", "refusal"]


# --- brief case 4 / plan 46: caps at five, reserves the slot, fills from baseline ----------------------------


def test_smoke_caps_at_five_reserves_the_out_of_scope_slot_and_reports_omissions(tmp_path):
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]
    s = spec(specialists=[specialist(n, f"{n} question one") for n in names], oos=["Unrelated question"])
    plan = R.build_router_plan(s)
    _, out = render(tmp_path, s)
    _, smoke = datasets(out)
    assert len(smoke) == 5 and smoke[-1]["expected_route"] == "refusal", "the out-of-scope slot must be kept"
    assert [r["expected_route"] for r in smoke[:4]] == names[:4]
    assert plan.omitted_specialists == ("echo", "foxtrot", "golf") and plan.missing_out_of_scope is False
    assert any("echo" in w for w in plan.warnings)


def test_two_examples_for_one_specialist_still_reach_the_two_row_minimum_from_baseline(tmp_path):
    s = spec(specialists=[specialist("billing", "Why was I charged twice?", "Where is my statement?")])
    _, out = render(tmp_path, s)
    base, smoke = datasets(out)
    assert len(base) == 2 and [r["query"] for r in smoke] == ["Why was I charged twice?", "Where is my statement?"]


def test_smoke_stays_small_when_it_already_has_the_minimum_and_does_not_pad_to_the_cap(tmp_path):
    s = spec(specialists=[specialist("billing", "b1", "b2", "b3"), specialist("fraud", "f1", "f2")], oos=["o1", "o2"])
    plan = R.build_router_plan(s)
    _, out = render(tmp_path, s)
    base, smoke = datasets(out)
    assert [r["query"] for r in smoke] == ["b1", "f1", "o1"] and len(base) == 7
    assert plan.smoke_ids == ("c001", "c004", "c006")


def test_fill_takes_unselected_baseline_rows_in_baseline_order_only_up_to_the_minimum(tmp_path):
    s = spec(specialists=[specialist("billing", "b1", "b2", "b3")])
    _, out = render(tmp_path, s)
    _, smoke = datasets(out)
    assert [r["query"] for r in smoke] == ["b1", "b2"], "fills to two rows, not to the cap"


def test_smoke_is_a_byte_exact_subset_of_baseline(tmp_path):
    s = spec(oos=["o1"])
    _, out = render(tmp_path, s)
    base = rd(out / "evals" / "datasets" / "baseline.jsonl").split("\n")
    smoke = rd(out / "evals" / "datasets" / "ci-smoke.jsonl").split("\n")
    assert smoke[-1] == "" and all(line in base for line in smoke if line)


def test_smoke_is_stable_across_renders(tmp_path):
    s = spec(oos=["o1"], cases=[{"query": "extra", "expected_route": "fraud"}])
    render(tmp_path, s, "a")
    render(tmp_path, s, "b")
    assert len(snapshot(tmp_path / "a")) >= 10, "nothing meaningful was rendered"
    assert snapshot(tmp_path / "a") == snapshot(tmp_path / "b")


# --- brief case 5 / plan 49: nothing invented; missing coverage reported -------------------------------------


def test_no_out_of_scope_query_is_invented_and_the_missing_coverage_is_reported(tmp_path):
    s = spec()
    plan = R.build_router_plan(s)
    _, out = render(tmp_path, s)
    base, smoke = datasets(out)
    assert all(r["expected_route"] != "refusal" for r in base + smoke), "no refusal query may be fabricated"
    assert {r["query"] for r in base} == {"Why was I charged twice?", "I did not make this purchase"}
    assert plan.missing_out_of_scope is True and any("out-of-scope" in w for w in plan.warnings)
    assert "no out-of-scope" in rd(out / "evals" / "README.md").lower()


def test_supplied_out_of_scope_coverage_clears_the_warning(tmp_path):
    plan = R.build_router_plan(spec(oos=["Tell me a joke"]))
    assert [c.expected_route for c in plan.cases] == ["billing", "fraud", "refusal"]
    assert plan.missing_out_of_scope is False and not any("out-of-scope" in w for w in plan.warnings)


def test_a_refusal_case_in_evaluation_cases_counts_as_out_of_scope_coverage():
    s = spec(cases=[{"query": "Sing me a song", "expected_route": "refusal"}])
    plan = R.build_router_plan(s)
    assert len(plan.cases) == 3, "the plan must hold the two examples plus the supplied refusal case"
    assert plan.cases[-1].query == "Sing me a song" and plan.cases[-1].expected_route == "refusal"
    assert plan.missing_out_of_scope is False


# --- brief case 6 / plan 64: conflicting routes rejected before output ---------------------------------------


def test_a_forged_plan_with_conflicting_routes_is_still_rejected_by_the_renderer(tmp_path):
    s = spec()
    forged = A.AgentSpec(**{**s.__dict__, "evaluation": A.EvaluationConfig(
        judge_model="j", min_pass_rate=0.8, evaluators=("relevance",), dataset_status="approved",
        cases=({"query": "Why was I charged twice?", "expected_route": "fraud"},))})
    with pytest.raises(E.EvalAssetError, match="conflict"):
        render(tmp_path, forged)
    assert not (tmp_path / "out").exists()


# --- plan 48: the reserved refusal route ---------------------------------------------------------------------


def test_refusal_expectations_use_the_reserved_route_and_it_matches_the_router_template(tmp_path):
    _, out = render(tmp_path, spec(oos=["Tell me a joke"]))
    base, _ = datasets(out)
    assert [r["expected_route"] for r in base if "joke" in r["query"]] == ["refusal"]
    source = (ROUTER_SRC / "agent.py").read_text(encoding="utf-8")
    assert f'REFUSAL_ROUTE = "{R.REFUSAL_ROUTE}"' in source and R.REFUSAL_ROUTE == "refusal"


# --- validity: a router needs routes and enough queries ------------------------------------------------------


def test_a_case_without_an_expected_route_is_rejected_for_a_router(tmp_path):
    s = spec(cases=[{"query": "Something new with no route"}])
    with pytest.raises(E.EvalAssetError, match="expected_route"):
        render(tmp_path, s)
    assert not (tmp_path / "out").exists()


def test_a_router_with_fewer_than_two_queries_is_rejected(tmp_path):
    s = spec(specialists=[specialist("billing", "Only one question")])
    with pytest.raises(E.EvalAssetError, match="at least two"):
        render(tmp_path, s)
    assert not (tmp_path / "out").exists()


def test_a_router_without_specialists_is_rejected(tmp_path):
    with pytest.raises(E.EvalAssetError, match="specialist"):
        render(tmp_path, spec(specialists=[], oos=["a", "b"]))


def test_a_case_restating_a_specialist_example_with_the_same_route_appears_once_in_source_order(tmp_path):
    cases = [{"query": "  Why   was I charged twice?  ", "expected_route": "billing"},
             {"query": "Brand new question", "expected_route": "fraud"}]
    _, out = render(tmp_path, spec(oos=["Tell me a joke"], cases=cases))
    base, _ = datasets(out)
    assert [r["query"] for r in base] == [
        "Why was I charged twice?", "I did not make this purchase", "Tell me a joke", "Brand new question"]
    assert [r["case_id"] for r in base] == ["c001", "c002", "c003", "c004"]


# --- the generated router eval tree --------------------------------------------------------------------------


def test_the_router_tree_adds_the_routing_gate_and_its_tests_to_the_shared_assets(tmp_path):
    result, out = render(tmp_path, spec(oos=["Tell me a joke"]))
    files = sorted(snapshot(out))
    assert files == sorted([
        "evals/.gitignore", "evals/README.md", "evals/datasets/baseline.jsonl", "evals/datasets/ci-smoke.jsonl",
        "evals/evals.yaml", "evals/requirements.txt", "evals/run_release_evaluation.py", "evals/tests.yaml",
        "evals/tests/test_release_evaluation.py", "run_routing_evaluation.py",
        "tests/test_routing_evaluation.py"])
    assert sorted(f.relative_to(out).as_posix() for f in result.files) == files


def test_router_runner_files_are_byte_identical_to_the_template_sources(tmp_path):
    _, out = render(tmp_path, spec(oos=["o"]))
    pairs = {"run_routing_evaluation.py": ROUTER_SRC / "run_routing_evaluation.py",
             "tests/test_routing_evaluation.py": ROUTER_SRC / "tests" / "test_routing_evaluation.py"}
    for rel, src in pairs.items():
        assert (out / rel).is_file(), f"the renderer did not write {rel}"
        assert (out / rel).read_bytes() == src.read_bytes(), rel


def test_a_missing_router_source_asset_fails_before_any_output(tmp_path):
    fake = tmp_path / "skill"
    shutil.copytree(SKILL / "hosted", fake / "hosted", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (fake / "hosted" / "patterns" / "router-specialists" / "run_routing_evaluation.py").unlink()
    before = snapshot(fake)
    with pytest.raises(E.EvalAssetError, match="run_routing_evaluation.py"):
        E.render_eval_tree(spec(oos=["o"]), tmp_path / "out", skill_root=fake)
    assert not (tmp_path / "out").exists() and snapshot(fake) == before


def test_the_release_gate_accepts_a_rendered_router_tree_but_never_releases_it_on_quality_alone(tmp_path):
    _, out = render(tmp_path, spec(oos=["Tell me a joke"], cases=[{"query": "q3", "expected_route": "fraud"}]))
    assert (out / "evals" / "evals.yaml").is_file(), "the renderer did not write evals.yaml"
    sys.path.insert(0, str(SKILL / "hosted" / "assets" / "evals"))
    try:
        import run_release_evaluation as gate
        config = gate.load_config(out / "evals" / "evals.yaml")
        assert config.pattern == "router-specialists" and len(config.rows) == 4
        assert "expected_route" in config.rows[0], "route metadata survives into the gate's rows"
        assert gate.build_request(config, "card-router", "1").items[0].keys() <= {"case_id", "query", "context"}
    finally:
        sys.path.remove(str(SKILL / "hosted" / "assets" / "evals"))
        sys.modules.pop("run_release_evaluation", None)


def test_the_routing_gate_reads_the_rendered_baseline_dataset(tmp_path):
    s = spec(oos=["Tell me a joke"])
    _, out = render(tmp_path, s)
    assert (out / "evals" / "datasets" / "baseline.jsonl").is_file(), "the renderer did not write the baseline"
    sys.path.insert(0, str(ROUTER_SRC))
    try:
        import run_routing_evaluation as gate
        cases = gate.load_cases(out / "evals" / "datasets" / "baseline.jsonl", ("billing", "fraud", "refusal"))
        assert [c.expected_route for c in cases] == ["billing", "fraud", "refusal"]
    finally:
        sys.path.remove(str(ROUTER_SRC))
        sys.modules.pop("run_routing_evaluation", None)


def test_the_generated_router_tests_run_outside_the_skill_tree_with_pythonpath_unset(tmp_path):
    _, out = render(tmp_path, spec(oos=["Tell me a joke"]))
    assert out.is_dir(), "the renderer did not write the tree"
    for name in ("agent.py", "main.py", "specialists.json"):
        shutil.copy(ROUTER_SRC / name, out / name)
    assert (out / "tests" / "test_routing_evaluation.py").is_file(), "the renderer did not write the tests"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    run = subprocess.run([sys.executable, "-m", "pytest", "tests", "evals/tests", "-q", "--no-header", "-p", "no:cacheprovider"],
                         cwd=out, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout[-1800:] + run.stderr[-800:]
    assert str(SKILL) not in run.stdout


def test_the_generated_routing_gate_blocks_from_a_copy_without_a_model_endpoint(tmp_path):
    _, out = render(tmp_path, spec(oos=["Tell me a joke"]))
    assert out.is_dir(), "the renderer did not write the tree"
    for name in ("agent.py", "main.py", "specialists.json"):
        shutil.copy(ROUTER_SRC / name, out / name)
    assert (out / "run_routing_evaluation.py").is_file(), "the renderer did not write the routing gate"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "FOUNDRY_PROJECT_ENDPOINT")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    run = subprocess.run([sys.executable, "run_routing_evaluation.py"], cwd=out, env=env, capture_output=True, text=True)
    assert run.returncode == 2 and "FOUNDRY_PROJECT_ENDPOINT" in run.stderr, (run.stdout[-500:], run.stderr[-800:])


def test_the_readme_states_that_local_routing_evidence_does_not_certify_a_deployed_router(tmp_path):
    _, out = render(tmp_path, spec(oos=["o"]))
    text = rd(out / "evals" / "README.md")
    assert "does not certify a deployed router" in text and "route-evidence adapter" in text


def test_tests_yaml_for_a_router_carries_the_expected_route(tmp_path):
    _, out = render(tmp_path, spec(oos=["Tell me a joke"]))
    data = yaml.safe_load(rd(out / "evals" / "tests.yaml"))
    assert [d["expected_route"] for d in data] == ["billing", "fraud", "refusal"]
