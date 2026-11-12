# 0006. Container sizing

- Status: Accepted
- Date: 2026-11-11
- Agent: card-dispute-router
- Related: 0001-agent-type.md

## Context

How much CPU and memory?

## Decision

0.25 CPU and 0.5Gi memory.

## Options considered

- **0.25 / 0.5Gi (default)** (chosen): Cheapest. Fine for a POC. Cold start took about 12 s in the POC.
- **1 / 2Gi or 2 / 4Gi**: More headroom for heavier tools or concurrency, at higher cost.

## Reason

Reason not recorded.

## Consequences

- Cheapest. Fine for a POC. Cold start took about 12 s in the POC.
