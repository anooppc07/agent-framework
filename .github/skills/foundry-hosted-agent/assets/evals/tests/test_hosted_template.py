"""Offline tests for the hosted-agent eval template: dataset/tool contract and runner logic.

No Azure credentials, no mocks: real files and the real runner/evaluator functions.
Run from the evals/ folder:  python -m pytest tests/ -v
"""

import json
import re
from pathlib import Path

import pytest

import run_evaluation as runner
from evaluators.tools import TOOL_DEFINITIONS, get_tool_evaluators

EVALS_DIR = Path(__file__).resolve().parent.parent
DATASETS_DIR = EVALS_DIR / "datasets"
DATASETS = ["ci-smoke.jsonl", "baseline.jsonl"]
MODEL = "test-deployment"
TOOL_NAME = "echo"  # the sample tool in agent.py


def _rows(name: str) -> list[dict]:
    text = (DATASETS_DIR / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# --- dataset: empty / null / boundary ---------------------------------------------------------


@pytest.mark.parametrize("dataset", DATASETS)
def test_queries_are_non_empty_strings(dataset):
    for i, row in enumerate(_rows(dataset), start=1):
        q = row.get("query")
        assert isinstance(q, str) and q.strip(), f"{dataset}:{i} query is null/empty: {q!r}"


def test_dataset_size_boundaries():
    smoke, baseline = _rows("ci-smoke.jsonl"), _rows("baseline.jsonl")
    assert 2 <= len(smoke) <= 5, f"ci-smoke must stay a small gate, has {len(smoke)} rows"
    assert len(baseline) >= len(smoke), "baseline must be at least as large as ci-smoke"


# --- tool contract ----------------------------------------------------------------------------


@pytest.mark.parametrize("dataset", DATASETS)
def test_tool_definitions_flat_shape(dataset):
    for i, row in enumerate(_rows(dataset), start=1):
        for tool in row.get("tool_definitions", []):
            assert {"name", "description", "parameters"} <= tool.keys(), f"{dataset}:{i} {tool}"
            assert "function" not in tool, f"{dataset}:{i} uses the rejected nested wrapper"


@pytest.mark.parametrize("dataset", DATASETS)
def test_dataset_tool_name_matches_agent_tool(dataset):
    for i, row in enumerate(_rows(dataset), start=1):
        names = [t["name"] for t in row["tool_definitions"]]
        assert names == [TOOL_NAME], f"{dataset}:{i} tool names {names} != ['{TOOL_NAME}']"


def test_tools_module_matches_agent_tool():
    assert [t["name"] for t in TOOL_DEFINITIONS] == [TOOL_NAME]


def test_tool_evaluators_use_deployment_name():
    for c in get_tool_evaluators(MODEL):
        assert c["initialization_parameters"] == {"deployment_name": MODEL}


# --- placeholders -----------------------------------------------------------------------------

AUTHOR_TOKEN = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")  # {{item.query}} / {{sample.*}} are Foundry's


def test_no_author_time_placeholders_left():
    skip = {"__pycache__", ".pytest_cache", "results"}
    hits = []
    for p in EVALS_DIR.rglob("*"):
        if p.is_file() and not skip & set(p.parts) and p.suffix in {".py", ".md", ".jsonl", ".toml", ".txt"}:
            hits += [f"{p.relative_to(EVALS_DIR)}: {m}" for m in AUTHOR_TOKEN.findall(p.read_text(encoding="utf-8"))]
    assert not hits, hits


# --- runner wiring ----------------------------------------------------------------------------


def test_criteria_have_no_groundedness():
    names = {c["name"] for c in runner.build_testing_criteria(MODEL)}
    assert names == {
        "coherence", "fluency", "relevance",
        "violence", "sexual", "self_harm", "hate_unfairness",
        "task_adherence", "intent_resolution",
        "tool_call_accuracy", "tool_selection", "tool_input_accuracy",
    }


# --- thresholds: boundary values --------------------------------------------------------------


@pytest.mark.parametrize(
    "scores, failing",
    [
        ({"violence": [1.0]}, False),        # exactly at the safety ceiling passes
        ({"violence": [1.01]}, True),        # just over fails
        ({"tool_selection": [0.95]}, False), # exactly at the binary floor passes
        ({"tool_selection": [0.94]}, True),
        ({"coherence": [3.8]}, False),       # exactly at the quality floor passes
        ({"coherence": [3.79]}, True),
    ],
)
def test_threshold_boundaries(scores, failing):
    assert bool(runner.evaluate_thresholds(scores, set())) is failing


def test_excluded_metric_does_not_gate():
    assert runner.evaluate_thresholds({"task_adherence": [0.0]}, {"task_adherence"}) == []


def test_empty_scores_and_empty_value_lists_produce_no_failures():
    assert runner.evaluate_thresholds({}, set()) == []
    assert runner.evaluate_thresholds({"coherence": []}, set()) == []


def test_thresholds_report_metrics_in_sorted_order(capsys):
    runner.evaluate_thresholds({"relevance": [5.0], "coherence": [5.0], "fluency": [5.0]}, set())
    printed = [line.split()[0] for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert printed == ["coherence", "fluency", "relevance"]


# --- error path: total invocation failure -----------------------------------------------------


def test_total_failure_detected_when_every_item_errored():
    msg = runner.check_for_total_invocation_failure({"violence": [0.0]}, 3, ["boom"])
    assert msg is not None and "boom" in msg and "3" in msg


def test_total_failure_not_reported_for_zero_items():
    assert runner.check_for_total_invocation_failure({}, 0, ["boom"]) is None


def test_total_failure_not_reported_when_a_response_metric_scored():
    assert runner.check_for_total_invocation_failure({"coherence": [4.0]}, 3, ["boom"]) is None


# --- evaluator groups (carried over from the prompt-agent suite) ------------------------------

from evaluators.quality import get_quality_evaluators
from evaluators.safety import get_safety_evaluators
from evaluators.task import get_task_evaluators


def _names(criteria):
    return {c["name"] for c in criteria}


def test_evaluator_group_names():
    assert _names(get_quality_evaluators(MODEL)) == {"coherence", "fluency", "relevance"}
    assert _names(get_safety_evaluators(MODEL)) == {"violence", "sexual", "self_harm", "hate_unfairness"}
    assert _names(get_task_evaluators(MODEL)) == {"task_adherence", "intent_resolution"}
    assert _names(get_tool_evaluators(MODEL)) == {"tool_call_accuracy", "tool_selection", "tool_input_accuracy"}


@pytest.mark.parametrize("factory", [get_quality_evaluators, get_safety_evaluators, get_task_evaluators])
def test_trace_mode_drops_data_mapping(factory):
    for c in factory(MODEL, for_traces=True):
        assert "data_mapping" not in c
