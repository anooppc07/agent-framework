# 0002. Model runtime

- Status: Accepted
- Date: 2026-11-11
- Agent: card-dispute-router
- Related: 0001-agent-type.md

## Context

Which model, which version, how much reasoning?

## Decision

The model deployment gpt-5-mini is named in azure.yaml.

## Options considered

- **Deploy-time placeholder (prompt default)**: The model is chosen per environment at deploy. One definition serves dev and demo, but nothing in the repo says which model ran.
- **Pinned deployment name**: Reproducible, but the deployment must exist in every target project.
- **Hosted: model named in `azure.yaml`** (chosen): `azd provision` would create it if absent; with an existing project, name a deployment that already exists and do not provision.
- **Reasoning effort low / medium / high (prompt)**: Higher effort raises quality and latency/cost. Omit it for non-reasoning models.

## Reason

Reason not recorded.

## Consequences

- `azd provision` would create it if absent; with an existing project, name a deployment that already exists and do not provision.
