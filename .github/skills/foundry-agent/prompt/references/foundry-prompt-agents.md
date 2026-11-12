---
description: 'Concepts and contracts for declarative Azure AI Foundry prompt agents used by the foundry-agent skill (prompt type)'
---

# Foundry Prompt Agents — Concepts and Contracts

A **declarative Foundry prompt agent** is an agent whose definition has `kind: prompt`. It is a
model, a set of instructions, and zero or more tools, registered in a Foundry project. There is **no
container and no agent code** — everything the agent does is governed by its `instructions` string
and its `tools` array.

This is the opposite of a *hosted* agent (a container image behind the `invocations` protocol).
Hosted agents are built and pushed; prompt agents are **published declaratively** to the Foundry
Agents data-plane REST API.

## The agent definition

`<name>.agent.json` is literally the body of `POST {project}/agents?api-version=v1`. Its nested
`definition` object is also the body of `POST {project}/agents/{name}/versions?api-version=v1` when
publishing a new version.

```jsonc
{
  "name": "<name>",
  "description": "<one-line capability>",
  "metadata": { "project": "...", "surface": "..." },
  "definition": {
    "kind": "prompt",
    "model": "__AZURE_AI_MODEL_DEPLOYMENT_NAME__",
    "instructions": "__INSTRUCTIONS__",
    "reasoning": { "effort": "medium" },
    "tools": [ /* 0..n tool blocks — see tool-shapes.md */ ]
  }
}
```

* `model` — a model **deployment name** in the Foundry project. Keep it as a deploy-time placeholder
  so dev/demo/prod can each bind a different deployment.
* `instructions` — injected from `instructions.md` at deploy time (so the prose reads and reviews as
  Markdown rather than a JSON-escaped blob).
* `reasoning.effort` — only for reasoning models; omit otherwise.
* `tools` — the grounding/action surface. Empty array for a model-only agent.

## How a frontend consumes it

The agent is called through the OpenAI-compatible **Responses API** with an `agent_reference`:

```
POST {project-endpoint}/openai/v1/responses
{
  "agent_reference": { "type": "agent_reference", "name": "<name>" },
  "conversation": "conv_...",
  "input": [ { "role": "user", "content": "..." } ],
  "stream": true
}
```

The `agent_reference` omits `version`, so it always resolves to the **latest** published version.
When fronting this through a gateway (App Gateway → APIM → Foundry), two rules matter:

1. **Pass the caller's `Authorization` header through untouched** if the agent's tools broker
   credentials On-Behalf-Of the signed-in user. Overwriting it with a managed-identity token
   collapses every caller into one identity.
2. **Do not buffer the response** — Responses streaming is SSE.

## Versioning

Foundry agents are versioned. The deploy script creates the agent on first run and publishes a **new
version** on every subsequent run. A `kind` change on an existing name (e.g. `hosted` → `prompt`) is
rejected with "Agent kind mismatch" and requires a delete-and-recreate — the deploy script handles
this cutover idempotently.

## Deploy path

Prompt agents are declarative, so there is no image build. `azd ai agent` only covers *hosted*
agents, so the deploy talks to the Agents data-plane REST API directly via `az rest` with
`--resource https://ai.azure.com`:

```
POST {project}/agents?api-version=v1                    # first deploy
POST {project}/agents/{name}/versions?api-version=v1    # subsequent deploys
```

## Behavior guardrails live only in the instructions

Because there is no code, `instructions.md` is the whole behavior contract. Two rules usually carry
disproportionate weight and should be stated explicitly and defended:

* **Grounding.** If the agent is meant to answer only from a tool's data, the instructions are the
  only thing stopping the model from answering from general knowledge when the tool returns nothing.
  State "never guess, extrapolate, or fabricate" plainly.
* **Action boundary.** If the agent is read-only, say so. With no action tools, letting the model
  *narrate* an action means claiming to do something that never happened.

## References

* Azure AI Foundry Agents — agent definitions and the Responses API (Microsoft Learn).
* Foundry Evaluations — agent evaluators and trace-based evaluation (Microsoft Learn).
