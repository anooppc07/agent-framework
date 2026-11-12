# Hosted agent: custom store (Redis)

An Agent Framework agent (`kind: hosted`, Responses 2.0.0) that keeps its **responses, agent sessions, workflow
checkpoints and function approvals in a Redis the PortCo owns**, instead of Foundry's defaults. The model is reached
through the AI Gateway. Adapted from upstream `agent-framework/responses/26-custom-store`.

Use it when conversation state must live in a store you control (retention, residency, sharing across instances).
Do not use it just to persist state: Foundry's default stores need no infrastructure.

## Exception to sign off (DataX)

KB 01 lists Cosmos DB, AI Search and Storage as the Foundry landing zone's state; Redis appears only in the AI
Gateway landing zone and the Auto Ops app. Either DataX approves Redis in the Foundry LZ, or you swap the four
classes in `src/agent/redis_state_stores.py` and `redis_response_store.py` for Cosmos DB (same `ResponseProviderProtocol`,
`SessionStore`, `CheckpointStorage`, `FunctionApprovalStore` contracts). Listed in `../../_gateway/GATEWAY-CONTRACT.md`.

## Changes from upstream

| Upstream | Here | Rule |
|---|---|---|
| `FoundryChatClient(project_endpoint=...)` | `gateway_client.chat_client()` | R1, R3 |
| model deployment in the project | none | R1 |
| Redis `publicNetworkAccess: 'Enabled'` | `'Disabled'` + private endpoint into `snet-private-endpoints`, zone `privatelink.redis.azure.net` | R9 |
| `ENABLE_SENSITIVE_DATA` only | plus `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` (see `../observability`) | R7 |

## Trust boundary: read this before you deploy

- **Checkpoints are pickled.** Anyone who can write a key in this Redis can run code in the agent. Keep the instance
  private and give write access to the agent identity only. The post-deploy hook grants the built-in `default` access
  policy (read-write on all keys); tighten it if your tier supports a narrower one.
- The checkpoint store decodes with `allowed_types=frozenset()`: only built-in and framework types. If you add a
  workflow with your own state classes, that set must be extended deliberately (see `../human-in-the-loop/src/agent/main.py`).
- **Retention.** Only stored *responses* expire (`REDIS_RESPONSE_TTL_SECONDS`, default 24 h). Sessions, checkpoints and
  approvals are kept until deleted. Decide a retention rule and add it before holding real data.
- User isolation relies on the `x-agent-user-id` header the platform sets. Keys are namespaced by a SHA-256 of it.

## Fill in

`GATEWAY_MODELS_BASE_URL`, `GATEWAY_MODEL_NAME`, `GATEWAY_TOKEN_SCOPE`, and for the Bicep layer
`PRIVATE_ENDPOINT_SUBNET_ID` and `REDIS_PRIVATE_DNS_ZONE_ID` (both come from the platform stamp).
Replace `store_validation_tools.py` with your own tools once you have seen the stores work.

## Run locally (real Redis, fake gateway)

```bash
podman run -d --name scbx-redis -p 6379:6379 redis:7-alpine redis-server --save "" --appendonly no   # or docker compose up -d
python ../../_gateway/fake_gateway.py 8099 &
cd src/agent
export GATEWAY_MODELS_BASE_URL=http://127.0.0.1:8099/v1 GATEWAY_MODEL_NAME=logical-model \
       GATEWAY_TOKEN_SCOPE=https://management.azure.com/.default REDIS_URL=redis://localhost:6379/0 \
       OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false ENABLE_SENSITIVE_DATA=false
uv sync && uv run python main.py
curl -s localhost:8088/responses -H 'content-type: application/json' -H 'x-agent-user-id: alice' \
     -d '{"input":"Remember INC-2301.","conversation":{"id":"c1"}}'
```

A tool call needing approval: send `CALL approve_incident_change {"change_id":"CHG-1"}` (the fake model turns it into a
tool call). The response holds an `mcp_approval_request` with `server_label: agent_framework`; answer it with an
`mcp_approval_response`.

## Status

Run against a real Redis 7 (Podman) and the fake gateway: all four stores wrote keys, no key holds a raw user id,
a second user got a separate partition and no data from the first, an `always_require` tool paused for approval,
the approval ran the tool, another user could not use it, and **an approval created before killing the process was
approved by a new process**. Known behaviour: replaying an approved id returns `completed` with empty output and
one extra model call; the tool does not run again. **Not run:** Azure Managed Redis, Entra sign-in to Redis, the
post-deploy hook, `infra/redis/main.bicep` (the Bicep CLI is not available here, so it is uncompiled), TTL expiry,
and concurrent writes to one approval id. `azd` is not installed here.
