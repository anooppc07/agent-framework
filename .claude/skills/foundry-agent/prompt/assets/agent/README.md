# {{AGENT_NAME}} — declarative Foundry prompt agent

{{AGENT_DESCRIPTION}}

`{{AGENT_NAME}}` is a **declarative Foundry prompt agent** (`kind: prompt`): a model, a set of
instructions, and {{TOOL_SUMMARY}}, registered in the Foundry project. There is no container and no
agent code — its whole behavior lives in [instructions.md](instructions.md) and the tool wiring in
[{{AGENT_NAME}}.agent.json]({{AGENT_NAME}}.agent.json).

## Layout

| File | Role |
| --- | --- |
| [{{AGENT_NAME}}.agent.json]({{AGENT_NAME}}.agent.json) | The agent definition (model + tools). This is literally the `POST /agents` request body. |
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
- {{GROUNDING_PREREQ}}

### Locally

```powershell
cd {{OUTPUT_DIR}}/scripts
./deploy-agent.ps1 `
  -ProjectEndpoint "https://<res>.services.ai.azure.com/api/projects/<proj>" `
  -ModelDeploymentName "<model-deployment>"{{DEPLOY_EXTRA_ARGS}}
```

Add `-WhatIf` to render and print the payload without calling Azure. Override the agent name with
`-AgentName` — but keep it in sync with whatever the consumer sends as `agent_reference.name`.

### From GitHub Actions (optional)

If a pipeline was generated, it is staged at [workflows/deploy-agent.yml](workflows/deploy-agent.yml)
and is **not active**. It runs only on manual dispatch (`workflow_dispatch`): there is no push,
pull-request or schedule trigger. To use it:

1. Copy it to `.github/workflows/deploy-{{AGENT_NAME}}.yml` in the repository (or ask the deploy
   skill to install it with your approval).
2. Set the per-environment variables and the OIDC identity it names.
3. Run it from the Actions tab ("Run workflow"), or ask the deploy skill to dispatch it.

Anyone allowed to dispatch workflows in the repository can run it, so protect the `dev` and `demo`
environments with required reviewers.

### Versioning

Foundry agents are versioned. The script creates the agent on first run and publishes a **new
version** on every subsequent run. A consumer's `agent_reference` that omits `version` always
resolves to the latest.

## How a frontend calls it

```
POST {project-endpoint}/openai/v1/responses
{
  "agent_reference": { "type": "agent_reference", "name": "{{AGENT_NAME}}" },
  "conversation": "conv_...",
  "input": [ { "role": "user", "content": "..." } ],
  "stream": true
}
```

## Editing the instructions

Instructions live in [instructions.md](instructions.md). They are the agent's whole behavior
contract — there is no code to fall back on. Defend the grounding and action-boundary rules; they are
the only guardrails the agent has.
