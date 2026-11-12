---
title: Azure Service Advisor Evaluation Suite
description: Run offline and Foundry evaluations for Azure Service Advisor
---

## Azure Service Advisor Evaluation Suite

The evaluation suite checks grounded service recommendations, MCP tool use, safety, and response
quality for `azure-service-advisor`.

## Layout

| Path | Role |
| --- | --- |
| [datasets/ci-smoke.jsonl](datasets/ci-smoke.jsonl) | Grounded recommendation used by the deployment CI gate. |
| [datasets/baseline.jsonl](datasets/baseline.jsonl) | Hand-curated grounding cases for release candidates. |
| [evaluators/](evaluators/) | Evaluator configuration modules for quality, groundedness, safety, task adherence, and tools. |
| [run_evaluation.py](run_evaluation.py) | Runs dataset evaluations or evaluates recent production traces. |
| [tests/](tests/) | Offline tests for dataset and evaluator structure. |
| `results/` | Local score snapshots, excluded from version control. |

## Dataset schema

Each line is a JSON object:

| Field | Required | Purpose |
| --- | --- | --- |
| `query` | yes | The full user turn sent to the agent. |
| `ground_truth` | yes | Expected JSON response, used by the `similarity` evaluator. |
| `context` | yes | Representative MCP result used by the `groundedness` evaluator. |
| `category` | yes | Current cases use `grounding`. |
| `test_intent` | yes | What the case checks. |
| `tool_definitions` | yes | The `getAzureServiceName` tool contract used by tool evaluators. |

### Adding a case

1. Add one JSON object per line.
2. Set `context` to the MCP result for the input. Current examples use representative fixtures;
   replace them with results from your MCP server before relying on evaluation scores.
3. Keep `tool_definitions` aligned with the MCP runtime name and its actual input schema.
4. Run `pytest tests/ -v`.

## Running evaluations

```bash
pip install -r requirements.txt

# Offline structural tests (no Azure credentials)
pytest tests/ -v

# Run against the deployed agent
python run_evaluation.py --mode ci --dataset ci-smoke.jsonl
python run_evaluation.py --mode pre-deploy --dataset baseline.jsonl
python run_evaluation.py --mode post-deploy --lookback-hours 6
```

The live modes require `AZURE_AI_PROJECT_ENDPOINT` and `AZURE_AI_MODEL_DEPLOYMENT_NAME`.

## Evaluators and thresholds

| Group | Evaluators | Threshold family |
| --- | --- | --- |
| Quality | `coherence`, `fluency`, `relevance` | Likert scale from 1 to 5, average at least 3.8 |
| Groundedness | `groundedness`, `similarity` | Likert scale from 1 to 5, informational by default |
| Safety | `violence`, `sexual`, `self_harm`, `hate_unfairness` | Severity from 0 to 7, average at most 1 |
| Task | `task_adherence`, `intent_resolution` | Binary pass rate at least 0.95 / Likert |
| Tools | `tool_call_accuracy`, `tool_selection`, `tool_input_accuracy` | Binary pass rate at least 0.95 / Likert |

The deploy gate excludes `task_adherence`, `groundedness`, and `similarity` by default. These
metrics can produce false negatives for tool serialization or variable MCP results. See
`GATING_METRICS_EXCLUDED` in `run_evaluation.py`.
