# 0001. Agent type

- Status: Accepted
- Date: 2026-11-11
- Agent: card-dispute-router
- Related: None

## Context

Prompt agent or hosted agent?

## Decision

A hosted agent (router-specialists) running Microsoft Agent Framework in a Foundry container.

## Options considered

- **prompt**: Declarative: model, instructions and tool wiring only. No container, no code to maintain. Behavior can only change through `instructions.md` and the tool block.
- **hosted** (chosen): Your Python (Microsoft Agent Framework) code runs in a Foundry container. Full control over logic and tools, but you own the code, its dependencies and its deploy/role setup.

## Reason

Reason not recorded.

## Consequences

- Your Python (Microsoft Agent Framework) code runs in a Foundry container. Full control over logic and tools, but you own the code, its dependencies and its deploy/role setup.
