"""Offline structural tests for the datasets. No Azure credentials required.

Run: pytest tests/test_dataset.py -v
"""

import json
from pathlib import Path

import pytest

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"

# Categories a grounded agent's dataset exercises. Tailor to your domain.
VALID_CATEGORIES = {
    "grounding",
    "grounding_partial",
    "no_grounding",
    "action_refusal",
    "out_of_scope",
    "tool_usage",
    "response_style",
}

# Categories whose cases must carry a `context` value for the groundedness evaluator.
CONTEXT_REQUIRED_CATEGORIES = {"grounding", "grounding_partial", "tool_usage", "response_style"}


def _load(name: str) -> list[dict]:
    path = DATASETS_DIR / name
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:  # pragma: no cover - failure path
            pytest.fail(f"{name}:{i} is not valid JSON: {exc}")
    return rows


@pytest.mark.parametrize("dataset", ["ci-smoke.jsonl", "baseline.jsonl"])
def test_valid_jsonl(dataset: str):
    rows = _load(dataset)
    assert rows, f"{dataset} is empty"


@pytest.mark.parametrize("dataset", ["ci-smoke.jsonl", "baseline.jsonl"])
def test_required_fields(dataset: str):
    for i, row in enumerate(_load(dataset), start=1):
        assert row.get("query"), f"{dataset}:{i} missing 'query'"
        assert row.get("category") in VALID_CATEGORIES, (
            f"{dataset}:{i} has unknown category {row.get('category')!r}"
        )
        assert row.get("test_intent"), f"{dataset}:{i} missing 'test_intent'"


@pytest.mark.parametrize("dataset", ["ci-smoke.jsonl", "baseline.jsonl"])
def test_context_present_where_required(dataset: str):
    for i, row in enumerate(_load(dataset), start=1):
        if row.get("category") in CONTEXT_REQUIRED_CATEGORIES:
            assert row.get("context"), (
                f"{dataset}:{i} category {row['category']!r} needs a 'context' value "
                f"for the groundedness evaluator"
            )
