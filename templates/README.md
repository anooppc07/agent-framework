# SCBX Agent Templates

Nine starting points (one of them a family of five patterns) for building Azure AI Foundry agents that follow the SCBX AI Agent Platform architecture
(`docs/architecture-kb/`). Every template reaches models and tools **only through the AI Gateway**.

Read [`_gateway/GATEWAY-CONTRACT.md`](_gateway/GATEWAY-CONTRACT.md) first. It defines the rules (R1-R10), the
environment variables DataX supplies, and the exceptions that need sign-off.

## Choosing a template

| I need... | Template | Kind | Verification |
|---|---|---|---|
| A no-code agent that calls tools from one MCP server on the gateway | [`prompt-agent/mcp`](prompt-agent/mcp) | Prompt (`azd` YAML) | Static checks only |
| A no-code agent that declares functions my application runs | [`prompt-agent/tools`](prompt-agent/tools) | Prompt (`azd` YAML) | Static checks only |
| A no-code agent whose tools come from a shared, versioned toolbox (preview feature) | [`prompt-agent/toolbox`](prompt-agent/toolbox) | Prompt (`azd` YAML) | Static checks only |
| My own code, with traces and one trace ID end to end | [`hosted-agent/observability`](hosted-agent/observability) | Hosted (Agent Framework, Python) | Run locally |
| A draft, a human review, then approve, revise or reject | [`hosted-agent/human-in-the-loop`](hosted-agent/human-in-the-loop) | Hosted (Agent Framework, Python) | Run locally |
| Per-user memory that persists across sessions | [`hosted-agent/foundry-memory`](hosted-agent/foundry-memory) | Hosted (Agent Framework, Python) | Run locally; memory read/write not run |
| Conversation state in a store the PortCo owns | [`hosted-agent/custom-store`](hosted-agent/custom-store) | Hosted (Agent Framework, Python) | Run locally against Redis 7 |
| Several agents together: a sequential chain or a group chat, one session per agent | [`hosted-agent/orchestration`](hosted-agent/orchestration) | Hosted (Agent Framework, Python) | Run locally (fake model, tests); fake-gateway run and deploy not run |
| One conversation shared by several agents (handoff, group chat) with state in a Cosmos DB the PortCo owns, plus single-agent, reflection and sequential-pipeline samples | [`hosted-agent/shared-session`](hosted-agent/shared-session) | Hosted (handoff, group chat) and library samples | Run against real Cosmos DB and a real model, not through the gateway; not deployed |

If the behaviour fits in instructions plus tools, use a prompt agent. If you need your own code, loops, a workflow or
custom state, use a hosted agent. Prompt agents contain no code, so they are `azd` manifests.

"Static checks only" means the YAML parses and passes the template check below. "Run locally" means the agent ran against
the local fake gateway. **No template has been deployed to Azure.** See [Status](#status-and-limitations).

## How the templates apply the architecture rules

| Rule | Applies to | Implementation |
|---|---|---|
| Models only through the gateway (R1-R3) | All | Prompt agents use `model: ai-gateway/<model>`. Hosted agents build their client with [`_gateway/gateway_client.py`](_gateway/gateway_client.py), which calls the gateway Models API with a Microsoft Entra token. |
| Tools and MCP only through the gateway (R4-R6) | All | MCP URLs are `${GATEWAY_MCP_BASE_URL}/...`, reached through a project connection that uses the project managed identity. MCP tools are allow-listed, `require_approval: never` is limited to named read-only tools, built-in `web_search` and `code_interpreter` are removed, and no credentials are stored. |
| Content stays in the PortCo (R7) | Hosted | `ENABLE_SENSITIVE_DATA` and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` are both set to `false`. |
| One trace ID end to end (R8) | Hosted | The W3C `traceparent` header is forwarded to the gateway on model and MCP calls. |
| Private by default (R9) | `custom-store` | The Redis Bicep disables public network access and uses a private endpoint. Network enforcement itself is a platform responsibility. |
| Dev only (R10) | All | Templates deploy to a dev project. Production deployment goes through the shared DataX pipeline. |

R1 and R4 are enforced by the platform (private endpoints, firewall allow-list, Azure Policy). A template cannot enforce
them. It can only avoid creating a bypass.

## Exceptions that need DataX sign-off

| Template | Exception | To confirm |
|---|---|---|
| `hosted-agent/foundry-memory` | The memory store calls a chat and an embedding deployment inside the Foundry account, outside the gateway. | Both deployments are on the approved-model list. The PortCo accepts where conversation content is stored (see [Behaviour to be aware of](#behaviour-to-be-aware-of)). |
| `hosted-agent/custom-store` | Uses Azure Managed Redis, which the architecture KB does not list for the Foundry landing zone. | DataX approves Redis in the Foundry landing zone, or the four store classes are replaced with Cosmos DB. |
| `hosted-agent/shared-session` | Stores raw conversation text in Cosmos checkpoints, allow-lists one extra pickle type (`types:GenericAlias`), and gives the agent identity data-plane write on a PortCo-owned database. Cosmos DB itself is in the KB's Foundry landing-zone list. | DataX accepts all three before real data (see its README). |
| `prompt-agent/toolbox` | Requires the preview `microsoft_copilot_preview` harness. | The harness is available in the SCBX region. |

## Template check

```bash
python _gateway/check_templates.py .    # exit 0 = clean, 1 = findings, 2 = nothing to check or unreadable file
```

The check reports:

- a direct model client (`FoundryChatClient`, `get_openai_client`, `AzureOpenAI`, `AzureChatOpenAI`)
- an MCP tool not built through `gateway_client.mcp_tool`
- an MCP `server_url` or `RemoteTool` target that is not a `${GATEWAY_...}` placeholder
- a prompt agent model that does not start with `ai-gateway/`
- built-in tools (`web_search`, `code_interpreter`, `bing_grounding`)
- a bare `require_approval: never`
- stored credentials (`CustomKeys`, `ApiKey`, `credentials`)
- `ENABLE_SENSITIVE_DATA: true`
- Bicep with `publicNetworkAccess: 'Enabled'`
- unreplaced placeholders such as `<subscription-id>`

It is a regression check, not enforcement. It cannot see inside your own function code, and the check itself has no
automated tests. **It only looks at `<kind>/<name>/azure.yaml`.** The `hosted-agent/shared-session` templates sit one level
deeper, so the command above does not reach them; they were checked by running the same check functions over that folder
(73 Python files and both manifests, no finding). The check also reads only `.bicep` files for R9, and `shared-session` uses
Terraform, so its `public_network_access_enabled = false` was checked separately. Extending the glob or adding a Terraform rule
is a change to a shared tool and was not made.

## Running a hosted template without Azure

[`_gateway/fake_gateway.py`](_gateway/fake_gateway.py) stands in for the gateway (a Models API and one MCP tool). It logs each
request's path, whether a bearer token arrived, and the `traceparent`. It is not a model: it echoes your message, and
`CALL <tool> {json}` makes it request a tool call. Each hosted template's README has the exact commands.

## Behaviour to be aware of

1. **Message content is recorded unless explicitly disabled.** The hosting package (version `1.0.0b261106`) reads
   `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, defaults it to `true`, and ignores `ENABLE_SENSITIVE_DATA`. With the
   default, prompts, responses and tool arguments appear in traces. Every hosted template sets both variables to `false`.
   Re-check after any hosting package upgrade.
2. **Human-in-the-loop wire format.** A pending review is returned as a `function_call` item named `request_info`. The client
   replies with a `function_call_output` carrying `{"action": "approve" | "revise" | "reject"}`. A rejection is returned as a
   normal completed response whose text begins `REJECTED:`, because the host reports any exception as a generic failure that
   clients cannot tell apart from an outage. A reply with an unknown call ID or action is refused.
3. **Foundry memory fails quietly.** If the memory store is unreachable, unauthorised or deleted, the agent still answers
   `completed`, without memory. Alert on the log messages `Failed to retrieve static memories` and
   `Failed to search contextual memories`.
4. **Replayed approvals do nothing.** In `custom-store`, replaying an already-approved tool approval returns `completed` with
   empty output and does not run the tool again.
5. **Conversation content and data residency.** The `foundry-memory` template sends every user and assistant message to the
   memory store. This stays in the PortCo only where the Foundry account is PortCo-owned (Pattern 3). Under Pattern 1 or 2
   the account is in a DataX subscription, which conflicts with architecture rule 5 unless the PortCo accepts it.
6. **Checkpoints are pickled.** In `custom-store` and `human-in-the-loop`, anyone who can write to the checkpoint store can
   run code in the agent. Restrict write access to the agent identity.

## Status and limitations

- **Not deployed.** No template has been deployed to Azure. `azd` and the Bicep CLI were not available during development, so
  the Bicep is uncompiled and the `azd` flows are untested.
- **Gateway values are placeholders.** The architecture KB does not specify the gateway URL shapes, token scope or audience.
  DataX must supply `GATEWAY_MODELS_BASE_URL`, `GATEWAY_MCP_BASE_URL`, `GATEWAY_TOKEN_SCOPE` and `GATEWAY_TOKEN_AUDIENCE`.
- **Pre-release dependencies.** The hosted templates use Agent Framework core `1.25.0` and hosting `1.0.0b261106`, a beta.
  No `uv.lock` is committed. Pin versions before production use.
- **Partly verified.** Each template's README lists exactly what was and was not run.

## Not covered

Evaluations, CI/CD pipelines, API Center registration, cross-PortCo calls, and Content Safety and PII redaction (gateway
policies, not agent code).

## Attribution

Adapted from [microsoft-foundry/foundry-samples](https://github.com/microsoft-foundry/foundry-samples) (MIT licence).
