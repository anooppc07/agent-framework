"""Offline structural tests for the datasets. No Azure credentials required.

Run: pytest tests/test_dataset.py -v
"""

import json
from pathlib import Path

import pytest

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"

VALID_CATEGORIES = {"grounding"}

CONTEXT_REQUIRED_CATEGORIES = {"grounding"}


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


@pytest.mark.parametrize("dataset", ["ci-smoke.jsonl", "baseline.jsonl"])
def test_tool_definition_matches_mcp_contract(dataset: str):
    for i, row in enumerate(_load(dataset), start=1):
        definitions = row.get("tool_definitions")
        assert definitions and len(definitions) == 1, (
            f"{dataset}:{i} must define exactly one MCP tool"
        )
        tool = definitions[0]
        assert tool.get("name") == "getAzureServiceName", (
            f"{dataset}:{i} must use the MCP runtime tool name"
        )
        parameters = tool.get("parameters", {})
        assert set(parameters.get("properties", {})) == {"description", "cost"}, (
            f"{dataset}:{i} must define description and cost parameters"
        )
        assert set(parameters.get("required", [])) == {"description", "cost"}, (
            f"{dataset}:{i} must require both MCP tool parameters"
        )
