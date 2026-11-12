# card-dispute-router evaluation

Generated from the confirmed agent spec. `evals.yaml` is the canonical release-evaluation intent; the files under `datasets/` are derived from the same plan, so edit the spec and regenerate rather than editing them.

Adapted from github.com/DeepuMM/agent-deployment @ 2220a3c2e3964728aef4444048944db0ebf41ecc: judge model, minimum pass rate, explicit evaluators and dataset, with `tests.yaml` for smoke intent. The reference's APIM and prompt-agent deployment steps are not part of this tree.

## Status of operational values

UNCONFIGURED: judge model, minimum pass rate, evaluators, dataset approval. The release gate blocks (exit 2) until these are set from approved project decisions. Nothing here supplies a default.

## Files

| File | Purpose |
| --- | --- |
| `evals.yaml` | canonical intent: judge model, min pass rate, evaluators, cases |
| `tests.yaml` | smoke intent; substring checks are not a security or policy gate |
| `datasets/baseline.jsonl` | every approved case once, with a stable `case_id` |
| `datasets/ci-smoke.jsonl` | a capped subset of the baseline |
| `run_release_evaluation.py` | fail-closed hosted gate for one explicit candidate name and version |
| `tests/` | offline tests of the gate; no Azure access |
| `../run_routing_evaluation.py` | deterministic local routing gate |
| `tests/test_routing_evaluation.py` | offline tests of the routing gate |

## Running

```
pip install -r requirements.txt
python -m pytest tests -q
python run_release_evaluation.py --agent-name <name> --agent-version <explicit version>
```

The hosted gate needs `FOUNDRY_PROJECT_ENDPOINT` and an authorised sign-in. It evaluates exactly the named version every time, rejects results for another candidate, dataset or evaluator set, and exits 0 (pass), 1 (a threshold was missed) or 2 (invalid configuration or evidence). A result is evidence for that one run; it does not promote anything.

## Routing

`run_routing_evaluation.py` runs each case through the router and compares the route that actually ran with `expected_route` (`refusal` is the reserved refusal route). The route comes from execution, never from answer text. It does not certify a deployed router: that needs a validated route-evidence adapter that correlates observations to the deployed candidate, and generic quality scores are not routing accuracy. Hosted release of a router therefore stays blocked (exit 2) on quality scores alone.

