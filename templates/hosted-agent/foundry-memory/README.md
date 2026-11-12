# Hosted agent: Foundry memory

An Agent Framework agent (`kind: hosted`, Responses 2.0.0) with **persistent, per-user semantic memory** from a
Foundry Memory Store. Adapted from upstream `agent-framework/responses/13-foundry-memory`.

## Two model paths, kept apart

| Call | Path | Rule |
|---|---|---|
| The agent's own model call | `gateway_client.chat_client()` -> AI Gateway Models API | R1, R3 |
| The memory store's chat + embedding calls | inside the Foundry account (the store is a Foundry resource) | **exception** |

The memory provider only accepts a Foundry project client, so it cannot be routed through the gateway. KB 01 allows
*workload-specific* models to be called directly inside the account, which is what makes this an exception and not a
bypass. It still needs two sign-offs from DataX, listed in `../../_gateway/GATEWAY-CONTRACT.md`:

1. The two in-account deployments (chat + embedding) are on the Azure Policy approved-model list.
2. **Where conversation content lands.** `after_run` sends every user and assistant message to the store. In Pattern 3
   (PortCo-owned Foundry) that stays in the PortCo. In Pattern 1 or 2 the account sits in a DataX subscription, which
   conflicts with "prompts and responses stay in the PortCo" (README rule 5) unless the PortCo accepts it.
   The user-profile prompt in `provision_memory_store.py` already tells the store to skip sensitive data. Change
   it for your data classification; it does not enforce anything.

## Changes from upstream

| Upstream | Here |
|---|---|
| `FoundryChatClient(project_endpoint=...)` for the agent | `gateway_client.chat_client()` |
| memory provider reuses the chat client's project client | provider builds its own from `FOUNDRY_PROJECT_ENDPOINT` (the chat client is no longer a Foundry client) |
| a missing store name only logs a warning and runs with no memory | **fails at start** with a clear message |
| `uv sync --frozen` in the hooks | `uv sync`: no `uv.lock` is committed (see `src/agent/uv.toml`) |
| `ENABLE_SENSITIVE_DATA` only | plus `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` (see `../observability`) |

## A broken store looks like a healthy agent

If the store is unreachable, unauthorised or deleted, the provider logs a WARNING and the agent **still answers
`completed`**, just without memory. Verified here by pointing it at an unreachable host. Alert on the log lines
`Failed to retrieve static memories` and `Failed to search contextual memories`; do not rely on request errors.

## Roles

The agent's identity needs **Azure AI User** and **Cognitive Services OpenAI User** on the Foundry project, otherwise
memory writes fail with `401` and the store stays empty (and, per the above, nothing else breaks visibly).
Grant them to the hosted agent's `...-AgentIdentity` service principal after the first deploy.

## Fill in

`GATEWAY_MODELS_BASE_URL`, `GATEWAY_MODEL_NAME`, `GATEWAY_TOKEN_SCOPE`, and the memory deployments:
`AZURE_AI_MODEL_DEPLOYMENT_NAME`, `AZURE_AI_EMBEDDING_MODEL_DEPLOYMENT_NAME`, `MEMORY_CHAT_MODEL[_VERSION]`,
`MEMORY_EMBEDDING_MODEL[_VERSION]`, `MEMORY_STORE_NAME`.

## Deploy (dev project only)

```bash
azd env set ...          # the variables above
azd provision            # also runs the postprovision hook, which creates the memory store
azd deploy
azd ai agent invoke "My favourite colour is green."
azd ai agent invoke --new-conversation "What is my favourite colour?"
```

## Status

Verified by running the agent: it refuses to start when `MEMORY_STORE_NAME` is unset or still a `${...}` placeholder,
and it answers through the fake gateway when the store is unreachable (with the warnings above). **Not verified:**
reading and writing memories, per-user scoping (`{{$userId}}`), the provisioning hook and the role assignments.
They need a real Foundry project, and `azd` is not installed here. `provision_memory_store.py` and the hooks are
upstream's, changed only as described above.
