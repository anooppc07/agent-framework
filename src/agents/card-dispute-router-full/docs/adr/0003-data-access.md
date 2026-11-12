# 0003. Data access

- Status: Accepted
- Date: 2026-11-11
- Agent: card-dispute-router
- Related: 0001-agent-type.md

## Context

Where does the agent get its facts, and what can it call?

## Decision

No tools: every specialist is tool-less, so the agent cannot call or change anything.

## Options considered

- **fabric (prompt)**: A published Fabric data agent, queried on behalf of each caller. Needs a Fabric connection at deploy.
- **mcp (prompt)**: A remote MCP server. Needs its URL and auth at deploy.
- **custom (prompt)**: A function tool the consuming app implements. The agent cannot answer tool questions on its own.
- **none (prompt or hosted)** (chosen): Model-only. No tool evaluators, no grounding checks.
- **sample (hosted)**: One placeholder `echo` tool in `agent.py`. Replace it, and keep `evals` tool definitions in sync.

## Reason

Reason not recorded.

## Consequences

- Model-only. No tool evaluators, no grounding checks.
