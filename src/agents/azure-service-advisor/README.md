---
title: Azure Service Advisor
description: Deploy and evaluate the Azure Service Advisor Foundry prompt agent
---

## Azure Service Advisor

`azure-service-advisor` is a **declarative Foundry prompt agent** (`kind: prompt`): a model,
instructions, and the remote MCP tool `getAzureServiceName`, registered in the Foundry project.
There is no container and no agent code. Its behavior lives in [instructions.md](instructions.md)
and the tool wiring in
[azure-service-advisor.agent.json](azure-service-advisor.agent.json).

## Layout

| File | Role |
| --- | --- |
| [azure-service-advisor.agent.json](azure-service-advisor.agent.json) | The agent definition (model + tools). This is literally the `POST /agents` request body. |
| [instructions.md](instructions.md) | The agent's behavior contract, kept as prose. Injected into the definition at deploy time. |
| [scripts/deploy-agent.ps1](scripts/deploy-agent.ps1) | Idempotent deploy (renders, then creates the agent or publishes a new version). |
| [evals/](evals/) | Foundry evaluation suite (datasets, evaluators, orchestration, offline tests). |

Deploy-time placeholders (`__AZURE_AI_MODEL_DEPLOYMENT_NAME__` and `__MCP_SERVER_URL__`), plus
`__INSTRUCTIONS__` from `instructions.md`, are substituted by the deploy script.

## Deploy

Prompt agents are declarative: no container build, no image push. The deploy talks to the Foundry
Agents data-plane REST API directly.

### Prerequisites

- Azure CLI (`az login`) as a principal with access to the target Foundry project
- PowerShell 7 (`pwsh`)
- A remote MCP server URL for the `getAzureServiceName` tool

Set these variables in the GitHub `dev` and `demo` environments for the pipeline:

* `FOUNDRY_PROJECT_ENDPOINT`
* `AZURE_AI_MODEL_DEPLOYMENT_NAME`
* `MCP_SERVER_URL`
* `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` for OIDC login

### First deployment

```powershell
cd src/agents/azure-service-advisor/scripts
./deploy-agent.ps1 `
  -ProjectEndpoint "https://<res>.services.ai.azure.com/api/projects/<proj>" `
  -ModelDeploymentName "<model-deployment>" `
  -McpServerUrl "<remote-mcp-server-url>"
```

Add `-WhatIf` to render and print the payload without calling Azure. Override the agent name with
`-AgentName`; keep it in sync with the consumer's `agent_reference.name`.
Every MCP call requires approval, as configured in the agent definition.

### Versioning

Foundry agents are versioned. The script creates the agent on first run and publishes a **new
version** on every subsequent run. A consumer's `agent_reference` that omits `version` always
resolves to the latest.

## How a frontend calls it

```http
POST {project-endpoint}/openai/v1/responses
{
  "agent_reference": { "type": "agent_reference", "name": "azure-service-advisor" },
  "conversation": "conv_...",
  "input": [ { "role": "user", "content": "..." } ],
  "stream": true
}
```

## Editing the instructions

Instructions live in [instructions.md](instructions.md). They are the agent's whole behavior
contract. Defend the grounding and action-boundary rules; they are the only guardrails the agent has.
