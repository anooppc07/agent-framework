"""Offline shape tests for the evaluator config modules. No Azure credentials required.

These confirm each evaluator group returns the expected names and that trace-mode drops the
dataset-shaped data_mapping. Run: pytest tests/test_evaluators.py -v
"""

import pytest

from evaluators.groundedness import get_groundedness_evaluators
from evaluators.quality import get_quality_evaluators
from evaluators.safety import get_safety_evaluators
from evaluators.task import get_task_evaluators
from evaluators.tools import get_tool_evaluators

MODEL = "test-deployment"


def _names(criteria) -> set[str]:
    return {c["name"] for c in criteria}


def test_quality_names():
    assert _names(get_quality_evaluators(MODEL)) == {"coherence", "fluency", "relevance"}


def test_safety_names():
    assert _names(get_safety_evaluators(MODEL)) == {
        "violence", "sexual", "self_harm", "hate_unfairness"
    }


def test_groundedness_names():
    assert _names(get_groundedness_evaluators(MODEL)) == {"groundedness", "similarity"}


def test_task_names():
    assert _names(get_task_evaluators(MODEL)) == {"task_adherence", "intent_resolution"}


def test_tool_names():
    assert _names(get_tool_evaluators(MODEL)) == {
        "tool_call_accuracy", "tool_selection", "tool_input_accuracy"
    }


def test_tool_evaluators_use_deployment_name_param():
    # The three tool evaluators require `deployment_name`, not `model`.
    for c in get_tool_evaluators(MODEL):
        assert "deployment_name" in c["initialization_parameters"]


@pytest.mark.parametrize("factory", [get_quality_evaluators, get_safety_evaluators, get_task_evaluators])
def test_trace_mode_drops_data_mapping(factory):
    for c in factory(MODEL, for_traces=True):
        assert "data_mapping" not in c
