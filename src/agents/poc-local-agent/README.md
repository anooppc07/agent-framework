# poc-local-agent — declarative Foundry prompt agent

Answers questions about a fictional office supplies policy, for testing the agent creation skill.

`poc-local-agent` is a **declarative Foundry prompt agent** (`kind: prompt`): a model, a set of
instructions, and no tools (model-only), registered in the Foundry project. There is no container and no
agent code — its whole behavior lives in [instructions.md](instructions.md) and the tool wiring in
[poc-local-agent.agent.json](poc-local-agent.agent.json).

## Layout

| File | Role |
| --- | --- |
| [poc-local-agent.agent.json](poc-local-agent.agent.json) | The agent definition (model + tools). This is literally the `POST /agents` request body. |
| [instructions.md](instructions.md) | The agent's behavior contract, kept as prose. Injected into the definition at deploy time. |
| [scripts/deploy-agent.ps1](scripts/deploy-agent.ps1) | Idempotent deploy (renders, then creates the agent or publishes a new version). |
| [evals/](evals/) | Foundry evaluation suite (datasets, evaluators, orchestration, offline tests). |

Deploy-time placeholders (`__AZURE_AI_MODEL_DEPLOYMENT_NAME__`, and any grounding-connection
placeholders) — plus `__INSTRUCTIONS__`, replaced by `instructions.md` — are substituted by the
deploy script.

## Deploy

Prompt agents are declarative: no container build, no image push. The deploy talks to the Foundry
Agents data-plane REST API directly.

### Prerequisites

- Azure CLI (`az login`) as a principal with access to the target Foundry project
- PowerShell 7 (`pwsh`)
- No grounding connection: this agent has no tools and answers from the model only

### Locally

```powershell
cd src/agents/poc-local-agent/scripts
./deploy-agent.ps1 `
  -ProjectEndpoint "https://<res>.services.ai.azure.com/api/projects/<proj>" `
  -ModelDeploymentName "<model-deployment>"
```

Add `-WhatIf` to render and print the payload without calling Azure. Override the agent name with
`-AgentName` — but keep it in sync with whatever the consumer sends as `agent_reference.name`.

### Versioning

Foundry agents are versioned. The script creates the agent on first run and publishes a **new
version** on every subsequent run. A consumer's `agent_reference` that omits `version` always
resolves to the latest.

## How a frontend calls it

```
POST {project-endpoint}/openai/v1/responses
{
  "agent_reference": { "type": "agent_reference", "name": "poc-local-agent" },
  "conversation": "conv_...",
  "input": [ { "role": "user", "content": "..." } ],
  "stream": true
}
```

## Editing the instructions

Instructions live in [instructions.md](instructions.md). They are the agent's whole behavior
contract — there is no code to fall back on. Defend the grounding and action-boundary rules; they are
the only guardrails the agent has.
