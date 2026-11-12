# 0005. Evaluation

- Status: Accepted
- Date: 2026-11-11
- Agent: card-dispute-router
- Related: 0001-agent-type.md, 0003-data-access.md

## Context

How is quality measured, and what blocks a deploy?

## Decision

Evaluation suite generated from the approved cases.

## Options considered

- **Evals on (default)** (chosen): Datasets, evaluators and a runner ship with the agent. Costs model quota on each run.
- **Evals off**: Nothing measures regressions. Prompt agents also lose the deploy gate.
- **`task_adherence` not gating**: It can false-fail a correct tool call. Still reported. Re-enable once stable.
- **Gate on safety, quality, tool metrics**: A miss fails CI (prompt) or is reported by the runner (hosted).

## Reason

Reason not recorded.

## Consequences

- Datasets, evaluators and a runner ship with the agent. Costs model quota on each run.
