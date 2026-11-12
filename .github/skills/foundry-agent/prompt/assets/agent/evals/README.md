# {{AGENT_NAME}} — Foundry evaluation suite

Foundry Evaluations for the {{AGENT_NAME}} agent. Validates that the deployed agent behaves to its
instructions contract: stays in scope, grounds answers in its tool (when grounded), respects its
action boundary, and invokes its tool correctly.

## Layout

| Path | Role |
| --- | --- |
| [datasets/ci-smoke.jsonl](datasets/ci-smoke.jsonl) | A few critical policy-boundary cases for the deployment CI gate. |
| [datasets/baseline.jsonl](datasets/baseline.jsonl) | The full hand-curated evaluation set. Run manually for release candidates. |
| [evaluators/](evaluators/) | Evaluator config modules — `quality.py`, `groundedness.py`, `safety.py`, `task.py`, `tools.py`. |
| [run_evaluation.py](run_evaluation.py) | Orchestration. `pre-deploy`/`ci` run a dataset against the deployed agent; `post-deploy` evaluates recent production traces. Checks results against thresholds. |
| [tests/](tests/) | Offline unit tests — dataset structure and evaluator config shape. No Azure credentials required. |
| results/ | Local snapshot of each run's scores (gitignored). |

## Dataset schema

Each line is a JSON object:

| Field | Required | Purpose |
| --- | --- | --- |
| `query` | yes | The full turn sent to the agent. |
| `ground_truth` | no | Expected answer, used by the `similarity` evaluator. |
| `context` | conditionally | What the grounding tool returned for that turn, used by `groundedness`. Required for grounded categories — see `tests/test_dataset.py`. |
| `category` | yes | The scenario class (e.g. `grounding`, `no_grounding`, `action_refusal`, `out_of_scope`, `tool_usage`, `response_style`). |
| `test_intent` | yes | One-line note on what the case checks, for humans reviewing failures. |
| `tool_definitions` | grounded agents | The tool contract the tool evaluators reason over. Identical across rows; the name must match the tool's runtime name. |

### Adding a case

1. Append a one-line JSON object (no trailing comma).
2. For a grounded answer, fill `context` with what the tool actually returns for that input — the
   `groundedness` evaluator has nothing to check against otherwise. Prefer the comprehensive record,
   not just the one fact the query asks about; groundedness scores the whole response.
3. Run `pytest tests/test_dataset.py -v`.

## Running

```bash
pip install -r requirements.txt
# Offline structural tests (no Azure):
pytest tests/ -v

# Against the deployed agent (needs AZURE_AI_PROJECT_ENDPOINT, AZURE_AI_MODEL_DEPLOYMENT_NAME):
python run_evaluation.py --mode ci --dataset ci-smoke.jsonl       # CI gate
python run_evaluation.py --mode pre-deploy --dataset baseline.jsonl
python run_evaluation.py --mode post-deploy --lookback-hours 6    # trace monitoring
```

## Evaluators and thresholds

| Group | Evaluators | Threshold family |
| --- | --- | --- |
| Quality | `coherence`, `fluency`, `relevance` | 1–5 Likert, avg ≥ 3.8 |
| Groundedness | `groundedness`, `similarity` | 1–5 Likert (informational by default) |
| Safety | `violence`, `sexual`, `self_harm`, `hate_unfairness` | 0–7 severity, avg ≤ 1 |
| Task | `task_adherence`, `intent_resolution` | binary pass-rate ≥ 0.95 / Likert |
| Tools | `tool_call_accuracy`, `tool_selection`, `tool_input_accuracy` | binary pass-rate ≥ 0.95 / Likert |

Three score families come back from the service: **safety** (0–7, low is good), **quality/judge**
(1–5, high is good), and **binary process** metrics (per-item 0/1, averaged into a pass rate). Each
is gated against its own family threshold in `run_evaluation.py`.

### Metrics excluded from the gate

`task_adherence`, `groundedness`, and `similarity` are reported but **not** gating by default
(`GATING_METRICS_EXCLUDED` in `run_evaluation.py`):

- `task_adherence` can false-negative a correctly-tooled response because the service can flatten
  structured tool calls into text before the judge sees them.
- `groundedness`/`similarity` can false-negative when the grounding tool's own output is
  nondeterministic against a static dataset snapshot.

Re-enable gating for your project once these are stable in your environment.

### Tuning thresholds

`QUALITY_MIN_SCORE`, `SAFETY_MAX_SCORE`, and `BINARY_MIN_PASS_RATE` are conservative starting
points. If the gate produces persistent false positives, either fix the dataset row (is its
`ground_truth`/`context` accurate?) or adjust the threshold — and document the change and why in the
PR that touches it. Loosening a **safety** threshold especially should not happen without discussion.

## Grounding=none

If the agent has no tools, delete `evaluators/groundedness.py`, `evaluators/tools.py`, their imports
in `run_evaluation.py`, and the `context`/`tool_definitions` dataset columns. Keep quality, safety,
and `intent_resolution`.
